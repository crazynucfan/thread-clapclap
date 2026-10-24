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


if __name__ == '__main__': unittest.main()
