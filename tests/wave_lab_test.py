import array
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import wave
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import wave_lab as lab
import cnn_lab as cnn


def packet(sequence=0, identity=42):
    data = lab.PACKET.pack(b'CCW1', identity, sequence, sequence * 64,
                           *([0, 4095, 2048, 1340] * 16), 0)
    return data[:-4] + struct.pack('<I', zlib.crc32(data[:-4]))


class WaveTests(unittest.TestCase):
    def binary_dataset(self, directory, labels, negative_only=False):
        """Exercise annotation loading without optional numeric dependencies."""
        path = directory / 'session.wav'
        path.write_bytes(b'waveform fixture; frontend is mocked in this loader test')
        annotations = directory / 'session.labels.json'
        annotations.write_text(json.dumps({'schema': 1, 'recording': path.name,
            'wav_sha256': lab.digest(path), 'negative_only': negative_only,
            'events': [{'onset_ms': 2100 + i * 300, 'label': label}
                       for i, label in enumerate(labels)]}))
        manifest = directory / 'manifest.json'
        manifest.write_text(json.dumps({'schema': 1, 'recordings': [
            {'annotations': annotations.name, 'split': 'train'}]}))
        with patch.object(cnn, 'audio', return_value=(
                {'group': 'session-A', 'duration_s': 4}, [0] * 32000)), \
                patch.object(cnn, 'window', return_value=None):
            return cnn.dataset(manifest)[0]

    def test_binary_labels_preserve_legacy_negative_annotations(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = self.binary_dataset(Path(temporary),
                                         ['clap', 'non_clap', 'knock', 'click', 'other'])
        self.assertEqual([label for _, label in record['events']], [0, 1, 1, 1, 1])

    def test_verified_mixed_negatives_supply_only_non_clap_background(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = self.binary_dataset(Path(temporary), ['non_clap'], negative_only=True)
        self.assertTrue(record['negative_only'])
        self.assertGreater(len(record['events']), 1)
        self.assertTrue(all(label == 1 for _, label in record['events']))

    def test_binary_negatives_reject_claps_unknown_and_unreviewed_labels(self):
        for label in ('clap', 'unlabelled', 'unknown_sound'):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(ValueError):
                    self.binary_dataset(Path(temporary), [label], negative_only=True)

    def test_threshold_preserves_claps_despite_one_high_scoring_isolated_negative(self):
        result = cnn.choose_threshold(
            [(0.95, True), (0.96, True)],
            [(0.90, True), (0.90, True), (0.99, True)],
            [[(2500, 0.90, True), (2800, 0.90, True), (5000, 0.99, True)]])
        self.assertEqual(result['clap_recall'], 1)
        self.assertEqual(result['false_pairs'], 0)
        self.assertEqual(result['false_trigger_claps'], 1)
        self.assertGreater(result['threshold'], 0.90)
        self.assertLessEqual(result['threshold'], 0.95)

    def test_threshold_never_pairs_events_across_recordings(self):
        result = cnn.choose_threshold([(0.80, True)], [(0.99, True), (0.99, True)],
                                     [[(2500, 0.99, True)], [(2800, 0.99, True)]])
        self.assertEqual(result['clap_recall'], 1)
        self.assertEqual(result['false_pairs'], 0)
        self.assertEqual(result['false_trigger_claps'], 2)

    def test_threshold_requires_both_labels_and_verified_negative_recordings(self):
        for positive, negative, streams in (([], [(0.5, True)], [[]]),
                                            ([(0.5, True)], [], [[]]),
                                            ([(0.5, True)], [(0.5, True)], [])):
            with self.subTest(positive=positive, negative=negative, streams=streams):
                with self.assertRaises(ValueError):
                    cnn.choose_threshold(positive, negative, streams)

    def test_threshold_rejects_false_actions_in_isolated_clap_recording(self):
        result = cnn.choose_threshold([(0.9, True), (0.95, True)], [(0.1, False)], [[]],
            [{'truth': [2000, 3500], 'scores': [(2000, .9, True, 1),
                (2300, .85, True, .1), (3500, .95, True, 1)]}])
        self.assertGreater(result['threshold'], .85)
        self.assertEqual(result['stream_clap_recall'], 1)
        self.assertEqual(result['false_pairs'], 0)

    def test_threshold_prioritises_full_trigger_recall_over_manual_alignment(self):
        result = cnn.choose_threshold([(0.99, True)], [(0.1, False)], [[]],
            [{'truth': [2000], 'scores': [(2010, .8, True, 1)]}])
        self.assertLessEqual(result['threshold'], .8)
        self.assertEqual(result['stream_clap_recall'], 1)

    def test_fragmented_packets_and_text(self):
        decoder = lab.Decoder()
        result = []
        stream = b'SDK log\r\nWAVE_BEGIN 42 1000 16000 9\n' + packet() + packet(1)
        for value in stream: result.extend(decoder.feed(bytes([value])))
        samples = [item for item in result if item['type'] == 'samples']
        self.assertEqual([item['sequence'] for item in samples], [0, 1])
        self.assertEqual(samples[0]['samples'][:4], (0, 4095, 2048, 1340))
        self.assertEqual(decoder.bad_crc, 0)

    def test_corruption_and_missing_samples(self):
        decoder = lab.Decoder()
        bad = bytearray(packet()); bad[25] ^= 1
        items = decoder.feed(bytes(bad) + packet(1))
        self.assertEqual(decoder.bad_crc, 1)
        session = lab.Session(42, 4, lambda _: None)
        session.accept({'type': 'control', 'line': 'WAVE_BEGIN 42 1000 16000 9'})
        with self.assertRaises(ValueError): session.accept(items[-1])
        session = lab.Session(42, 4, lambda _: None)
        with self.assertRaises(ValueError): session.accept(lab.Decoder().feed(packet())[0])

    def test_session_fidelity_and_completion(self):
        output = bytearray()
        session = lab.Session(42, 4, output.extend)
        session.accept({'type': 'control', 'line': 'WAVE_BEGIN 42 1000 16000 9'})
        decoder = lab.Decoder()
        for sequence in range(1000): session.accept(decoder.feed(packet(sequence))[0])
        session.accept({'type': 'control', 'line': 'WAVE_END 42 1000 0'})
        self.assertTrue(session.complete)
        self.assertEqual(session.count, 64000)
        self.assertEqual(struct.unpack_from('<4h', output), (-32768, 32752, 0, -11328))
        self.assertEqual(session.low, 0); self.assertEqual(session.high, 4095)
        self.assertEqual(session.clipped / session.count, .5)
        with self.assertRaises(ValueError): session.accept(decoder.feed(packet(1000))[0])

    def test_incomplete_or_wrong_clock_end_rejected(self):
        for end in ('WAVE_END 42 1000 0', 'WAVE_END 42 0 1'):
            session = lab.Session(42, 4, lambda _: None)
            session.accept({'type': 'control', 'line': 'WAVE_BEGIN 42 1000 16000 9'})
            with self.assertRaises(ValueError): session.accept({'type': 'control', 'line': end})
        session = lab.Session(42, 4, lambda _: None)
        with self.assertRaises(ValueError): session.accept({'type': 'control', 'line': 'WAVE_ABORT 42 dma_overflow'})

    def test_private_output_and_no_clobber(self):
        with self.assertRaises(ValueError): lab.private_path(ROOT / 'public.wav')
        self.assertTrue(lab.private_path(ROOT / '.local/test.wav').is_relative_to(ROOT / '.local'))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'labels.json'
            cnn.write_json(path, {})
            with self.assertRaises(FileExistsError): cnn.write_json(path, {})

    def test_record_complete_and_incomplete_usb_sessions(self):
        class FakePort:
            missing = False
            def __init__(self, **kwargs): self.is_open = False; self.data = b''
            def open(self): self.is_open = True
            def close(self): self.is_open = False
            def reset_input_buffer(self): pass
            def write(self, command):
                if command.startswith(b'WAVE_CAPTURE'):
                    identity = int(command.split()[1])
                    self.data = f'WAVE_BEGIN {identity} 1000 16000 9\n'.encode()
                    self.data += b''.join(packet(i, identity) for i in range(1000) if not self.missing or i != 1)
                    self.data += f'WAVE_END {identity} 1000 {int(self.missing)}\n'.encode()
            def read(self, size):
                result, self.data = self.data[:size], self.data[size:]
                return result
        fake_serial = types.SimpleNamespace(Serial=FakePort, SerialException=OSError)
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules, serial=fake_serial), patch('sys.stdout', new_callable=io.StringIO):
            for missing in (False, True):
                FakePort.missing = missing
                path = Path(temporary) / ('broken.wav' if missing else 'complete.wav')
                args = argparse.Namespace(output=path, duration=4, port='fake', label='clap', group='session-A')
                if missing:
                    with self.assertRaises(ValueError): lab.record(args)
                    with self.assertRaises(ValueError): lab.load_wave(path)
                else:
                    lab.record(args)
                    metadata, samples = lab.load_wave(path)
                    self.assertEqual(len(samples), 64000)
                    self.assertTrue(metadata['complete'])
                    with self.assertRaises(ValueError): lab.record(args)
                    with path.open('r+b') as output:
                        output.seek(-1, 2); output.write(b'\x01')
                    with self.assertRaises(ValueError): lab.load_wave(path)

    def test_host_pair_rule(self):
        self.assertEqual(cnn.pairs([2000, 2300]), 1)
        self.assertEqual(cnn.pairs([2000]), 0)
        self.assertEqual(cnn.pairs([2000, 2300, 2600]), 0)
        self.assertEqual(cnn.pairs([2000, 2100]), 0)
        self.assertEqual(cnn.pairs([2000, 2600]), 0)
        self.assertEqual(cnn.pairs([2000, 2300, 3500, 3800]), 2)

    def test_immediate_pair_rule_emits_once_for_longer_burst(self):
        self.assertEqual(cnn.pair_groups([2000,2200,2400,2600,4000,4250], 'immediate'),
                         [[2000,2200],[4000,4250]])
        for gap in (160,200,250,300,599):
            self.assertEqual(cnn.pairs([2000,2000+gap], 'immediate'),1)
        for gap in (159,600):
            self.assertEqual(cnn.pairs([2000,2000+gap], 'immediate'),0)

    def test_immediate_threshold_selection_counts_triples_as_actions(self):
        result=cnn.choose_threshold([(0.95,True)],[(0.8,True)],
            [[(2000,.8,True),(2250,.8,True),(2500,.8,True)]],pair_policy='immediate')
        self.assertGreater(result['threshold'],.8)
        self.assertEqual(result['false_pairs'],0)

    def test_manifest_snapshot_preserves_annotation_locations_and_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);output=root/'experiment'/'manifest.json'
            original={'schema':1,'recordings':[{'annotations':'labels/session.json','split':'train'}]}
            cnn.save_manifest_snapshot(root/'manifest.json',original,output)
            snapshot=json.loads(output.read_text())
            self.assertEqual((output.parent/snapshot['recordings'][0]['annotations']).resolve(),
                             (root/'labels/session.json').resolve())
            self.assertEqual(original['recordings'][0]['annotations'],'labels/session.json')
            self.assertEqual(cnn.experiment_manifest(output.parent),output)

    def test_association_uses_attack_time_when_early_window_sees_future_clap(self):
        events = [(2580, .99, True, .1), (2814, .99, True, .01),
                  (2876, .99, True, .2), (2924, .98, True, .02),
                  (2976, .95, True, .01)]
        accepted = cnn.associate(events, .8)
        self.assertEqual(accepted, [2580, 2876, 2976])
        self.assertEqual(cnn.pair_groups(accepted), [[2580, 2876]])

    def test_association_does_not_chain_merge_real_claps(self):
        events = [(2000, .9, True, 1), (2100, .9, True, .1),
                  (2160, .9, True, 1), (2260, .9, True, .1)]
        self.assertEqual(cnn.associate(events, .8), [2000, 2160])
        self.assertEqual(cnn.pairs(cnn.associate(events, .8)), 1)

    def test_association_ignores_rejected_candidates_and_respects_boundary(self):
        events = [(2000, .1, True, 100), (2040, .9, False, 100),
                  (2100, .9, True, 1), (2220, .9, True, 2)]
        self.assertEqual(cnn.associate(events, .8), [2100, 2220])

    def test_temporal_matching_cannot_reuse_one_clap_or_pair(self):
        result = cnn.match_times([2100], [2000, 2200])
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(len(result['missed_expected']), 1)
        result = cnn.match_times([[2010, 2350], [3600, 3950]],
                                 [[2000, 2300], [3500, 3800]])
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(result['unmatched_detected'], [[3600, 3950]])

    def test_temporal_matching_maximises_matches_before_timing_accuracy(self):
        result = cnn.match_times([2090, 2150], [2000, 2100])
        self.assertEqual(len(result['matches']), 2)
        self.assertEqual(cnn.match_times([], [])['matches'], [])


if __name__ == '__main__': unittest.main()
