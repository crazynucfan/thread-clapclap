"""Optional full CNN plumbing check. Synthetic sounds only; no accuracy claim.

Run with the isolated tools/requirements-cnn.txt environment, not the capture
environment. All generated WAVs, annotations, and models live in a temp dir.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import cnn_lab as cnn
import wave_lab


def fixtures(directory):
    entries = []
    rng = np.random.default_rng(123)
    for split in ('train', 'validation', 'test'):
        count = 20 if split == 'train' else 5
        duration = 8 if split == 'train' else 4
        for label in cnn.CLASSES:
            path = directory / f'{split}-{label}.wav'
            samples = rng.normal(0, 8, duration * 16000)
            events = []
            for index in range(count):
                onset = 2400 + index * 250
                start = onset * 16
                age = np.arange(1000)
                if label == 'clap':
                    pulse = rng.normal(0, 4000, len(age)) * np.exp(-age / 90)
                elif label == 'knock':
                    pulse = np.sin(age * 2 * np.pi * 300 / 16000) * 4000 * np.exp(-age / 200)
                elif label == 'click':
                    pulse = np.zeros(len(age)); pulse[:3] = [6000, -4000, 2000]
                else:
                    pulse = np.sin(age * 2 * np.pi * 700 / 16000) * 200 * np.sin(age * np.pi / 1000)
                samples[start:start+len(age)] += pulse
                events.append({'onset_ms': onset, 'label': label})
            with wave.open(str(path), 'wb') as writer:
                writer.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                writer.writeframes(np.clip(samples, -32768, 32767).astype('<i2').tobytes())
            metadata = {'schema': 1, 'complete': True, 'duration_s': duration, 'frames': len(samples),
                        'group': split, 'label': label, 'wav_sha256': wave_lab.digest(path)}
            path.with_suffix('.wav.json').write_text(json.dumps(metadata))
            annotations = directory / f'{split}-{label}.labels.json'
            annotations.write_text(json.dumps({'schema': 1, 'recording': path.name,
                'wav_sha256': wave_lab.digest(path), 'negative_only': label != 'clap', 'events': events}))
            entries.append({'annotations': annotations.name, 'split': split})
    manifest = directory / 'manifest.json'
    manifest.write_text(json.dumps({'schema': 1, 'recordings': entries}))
    return manifest, entries


def main():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        manifest, entries = fixtures(directory)
        records = cnn.dataset(manifest)
        assert len(records) == 12
        # Refuse a source recording reused across splits, including copied
        # annotations and a group name that accidentally crosses a split.
        bad = directory / 'bad.json'
        bad.write_text(json.dumps({'schema': 1, 'recordings': entries + [{'annotations': entries[0]['annotations'], 'split': 'test'}]}))
        try: cnn.dataset(bad)
        except ValueError: pass
        else: raise AssertionError('dataset leakage was not rejected')
        output = directory / 'model'
        cnn.train(argparse.Namespace(manifest=manifest, output=output, seed=1, epochs=1))
        report = json.loads((output / 'report.json').read_text())
        assert report['model_bytes'] < 80 * 1024
        assert report['tensor_arena_bytes'] is None and report['esp32c6_inference_us'] is None
        assert report['test_negative_seconds'] > 0
        assert len(report['test_confusion_rows_actual']) == 4
        source = directory / 'header.cpp'
        source.write_text('#include "' + str(output / 'ClapModel.local.h') + '"\n'
                          'static_assert(clapModelLength == ' + str(report['model_bytes']) + ');\nint main(){}\n')
        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        str(source), '-o', str(directory / 'header')], check=True)
        print('PASS synthetic recording/label/split/train/INT8/full-record evaluation/C-array workflow')
        print('No measured clap recognition or ESP32-C6 resource result is claimed.')


if __name__ == '__main__': main()
