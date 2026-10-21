#!/usr/bin/env python3
"""Capture timestamped FFT features and replay the actual C++ detector locally."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
PACKET = struct.Struct('<4sIIII7fHHI')
MAGIC = b'CCF1'
FIELDS = ('session', 'sequence', 'sample_ms', 'uptime_ms', 'rms', 'peak',
          'high_ratio', 'flatness', 'concentration', 'clipped_fraction',
          'onset_flux', 'occupied_bins', 'flux_bins')

class Decoder:
    """Resynchronise around text logs and partial USB reads; validate every packet."""
    def __init__(self):
        self.buffer = bytearray()
        self.text = bytearray()
        self.bad_crc = 0

    def _text(self, data):
        self.text.extend(data)
        result = []
        while b'\n' in self.text:
            line, _, tail = self.text.partition(b'\n')
            self.text = bytearray(tail)
            line = line.decode('ascii', errors='replace').strip()
            if line.startswith('CAPTURE_'):
                result.append({'type': 'control', 'line': line})
        if len(self.text) > 4096:
            del self.text[:-256]
        return result

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while self.buffer:
            position = self.buffer.find(MAGIC)
            if position < 0:
                keep = next((n for n in (3, 2, 1) if self.buffer.endswith(MAGIC[:n])), 0)
                consume = len(self.buffer) - keep
                result.extend(self._text(self.buffer[:consume]))
                del self.buffer[:consume]
                break
            if position:
                result.extend(self._text(self.buffer[:position]))
                del self.buffer[:position]
            if len(self.buffer) < PACKET.size:
                break
            packet = bytes(self.buffer[:PACKET.size])
            if zlib.crc32(packet[:-4]) != struct.unpack_from('<I', packet, 52)[0]:
                self.bad_crc += 1
                del self.buffer[0]
                continue
            values = PACKET.unpack(packet)
            result.append({'type': 'frame', **dict(zip(FIELDS, values[1:-1]))})
            del self.buffer[:PACKET.size]
        return result

def validate_frame(frame, previous=None):
    for field in ('session', 'sequence', 'sample_ms', 'uptime_ms'):
        if type(frame.get(field)) is not int or not 0 <= frame[field] <= 0xffffffff:
            raise ValueError('Invalid integer: ' + field)
    for field in ('rms', 'peak', 'high_ratio', 'flatness', 'concentration', 'clipped_fraction', 'onset_flux'):
        value = frame.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid feature: ' + field)
        if field not in ('rms', 'peak') and value > 1.000001:
            raise ValueError('Ratio outside [0,1]: ' + field)
    for field in ('occupied_bins', 'flux_bins'):
        if type(frame.get(field)) is not int or not 0 <= frame[field] <= 117:
            raise ValueError('Invalid bin count: ' + field)
    if previous is None:
        if frame['sequence'] != 0:
            raise ValueError('Missing initial capture frame')
    elif (frame['session'] != previous['session'] or frame['sequence'] != previous['sequence'] + 1
          or ((frame['sample_ms'] - previous['sample_ms']) & 0xffffffff) != 8):
        raise ValueError('Capture has a session change, missing frame, or sample-clock discontinuity')

def detector_id():
    digest = hashlib.sha256()
    for name in ('ImpulseClapDetector.h', 'Config.h'):
        digest.update((ROOT / 'firmware' / 'ThreadClapClap' / name).read_bytes())
    return int(digest.hexdigest()[:8], 16)


def record(args):
    try:
        import serial
    except ImportError as error:
        raise ValueError('Install capture dependency: python -m pip install -r tools/requirements-audio.txt') from error
    destination = args.output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    session = secrets.randbelow(0xffffffff) + 1
    decoder = Decoder()
    previous = None
    expected_frames = args.duration * 125
    complete = False
    begin_seen = False
    port = serial.Serial(port=None, baudrate=115200, timeout=0.25, write_timeout=1)
    port.dtr = port.rts = False
    port.port = args.port
    with destination.open('x', encoding='utf-8') as output:
        def save(item):
            output.write(json.dumps(item, allow_nan=False) + '\n')
            output.flush()
        metadata = {'type': 'session', 'schema': 1, 'session': session, 'label': args.label,
              'expected_clicks': args.expected_clicks, 'duration_s': args.duration,
              'sample_rate': 16000, 'hop_samples': 128, 'classifier': 'burst-v1'}
        try:
            port.open()
            port.reset_input_buffer()
            port.write(f'CAPTURE {session} {args.duration}\n'.encode('ascii'))
            deadline = time.monotonic() + args.duration + 45
            print('Waiting for capture start. Matter actions are suppressed during capture.', flush=True)
            while time.monotonic() < deadline and not complete:
                for item in decoder.feed(port.read(4096)):
                    if item['type'] == 'frame':
                        if item['session'] != session:
                            continue
                        if not begin_seen:
                            raise ValueError('Capture start marker was lost')
                        validate_frame(item, previous)
                        previous = item
                        save(item)
                    else:
                        fields = item['line'].split()
                        if fields and fields[0] == 'CAPTURE_ERROR':
                            raise ValueError(item['line'])
                        if len(fields) < 2 or fields[1] != str(session):
                            continue
                        if fields[0] == 'CAPTURE_BEGIN':
                            if begin_seen or len(fields) != 5 or int(fields[2]) != expected_frames:
                                raise ValueError('Invalid capture start marker')
                            begin_seen = True
                            metadata['detector_build_id'] = int(fields[4])
                            save(metadata)
                            save(item)
                            print(f'Capturing {args.duration}s. Keep quiet for the first 2s and last 1s.', flush=True)
                        elif fields[0] == 'CAPTURE_ABORT':
                            raise ValueError('Firmware aborted capture: ' + ' '.join(fields[2:]))
                        elif fields[0] == 'CAPTURE_END':
                            if (len(fields) != 5 or int(fields[2]) != expected_frames or int(fields[3]) != 0
                                    or previous is None or previous['sequence'] + 1 != expected_frames or decoder.bad_crc):
                                raise ValueError('Capture lost frames or failed CRC; do not use it for accuracy evaluation')
                            complete = True
                            save(item)
                            save({'type': 'complete', 'frames': expected_frames, 'firmware_clicks': int(fields[4])})
                            print(f'Saved {expected_frames} complete frames; firmware detected {fields[4]} pair(s).', flush=True)
            if not complete:
                raise ValueError('Timed out before complete capture')
        except BaseException:
            save({'type': 'incomplete', 'frames': 0 if previous is None else previous['sequence'] + 1})
            raise
        finally:
            if port.is_open:
                if not complete:
                    try:
                        port.write(b'CAPTURE_STOP\n')
                    except serial.SerialException:
                        pass
                port.close()
    print(destination)

def load_capture(path):
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if (not items or items[0].get('type') != 'session' or items[0].get('schema') != 1
            or items[-1].get('type') != 'complete' or any(item.get('type') == 'incomplete' for item in items)):
        raise ValueError('Capture is incomplete or has an unsupported schema')
    frames = [item for item in items if item.get('type') == 'frame']
    previous = None
    for frame in frames:
        validate_frame(frame, previous)
        if frame['session'] != items[0]['session']:
            raise ValueError('Frame belongs to a different session')
        previous = frame
    if not frames or len(frames) != items[-1]['frames'] or len(frames) != items[0]['duration_s'] * 125:
        raise ValueError('Capture frame count does not match session duration')
    return items[0], frames, items[-1]

def replay(args):
    compiler = os.environ.get('CXX', 'g++')
    if not shutil.which(compiler):
        raise ValueError('A C++ compiler is required for detector replay')
    binary = ROOT / '.build' / 'audio_replay'
    binary.parent.mkdir(parents=True, exist_ok=True)
    dependencies = [ROOT / 'tools' / 'audio_replay.cpp', * (ROOT / 'firmware' / 'ThreadClapClap').glob('*.h')]
    if not binary.exists() or any(p.stat().st_mtime > binary.stat().st_mtime for p in dependencies):
        subprocess.run([compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
                        str(dependencies[0]), '-o', str(binary)], check=True)
    failed = False
    for path in args.files:
        metadata, frames, end = load_capture(path)
        keys = ('sample_ms', 'rms', 'peak', 'high_ratio', 'flatness', 'concentration',
                'clipped_fraction', 'occupied_bins', 'onset_flux', 'flux_bins')
        stream = ''.join(' '.join(str(frame[key]) for key in keys) + '\n' for frame in frames)
        command = [str(binary), args.classifier]
        if args.min_score is not None:
            if args.classifier != 'burst':
                raise ValueError('--min-score applies only to the burst classifier')
            command.append(str(args.min_score))
        run = subprocess.run(command, input=stream, text=True, capture_output=True, check=True)
        result = [json.loads(line) for line in run.stdout.splitlines()]
        summary = result[-1]
        if args.decisions:
            for item in result[:-1]:
                print(json.dumps({'file': str(path), **item}))
        expected = metadata.get('expected_clicks')
        matches = None if expected is None else summary['clicks'] == expected
        same_firmware = (args.classifier == 'burst' and args.min_score is None
                         and metadata.get('detector_build_id', 0) == detector_id())
        consistent = summary['clicks'] == end['firmware_clicks'] if same_firmware else None
        failed |= matches is False or consistent is False
        print(json.dumps({'file': str(path), 'label': metadata['label'], 'classifier': args.classifier,
                          'clicks': summary['clicks'], 'expected_clicks': expected, 'matched': matches,
                          'firmware_clicks': end['firmware_clicks'], 'firmware_consistent': consistent,
                          'duration_s': metadata['duration_s']}))
    return 1 if failed else 0

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('record', help='capture all FFT features; waveform audio is not stored')
    capture.add_argument('--port', required=True)
    capture.add_argument('--output', type=Path, required=True, help='use an ignored .local/audio/ path')
    capture.add_argument('--label', required=True)
    capture.add_argument('--expected-clicks', type=int, help='omit for an unlabelled observation')
    capture.add_argument('--duration', type=int, default=15, choices=range(4, 601), metavar='4..600')
    playback = commands.add_parser('replay', help='run the actual firmware classifier against complete captures')
    playback.add_argument('files', type=Path, nargs='+')
    playback.add_argument('--classifier', choices=('burst', 'legacy'), default='burst')
    playback.add_argument('--min-score', type=float)
    playback.add_argument('--decisions', action='store_true')
    args = parser.parse_args()
    if args.command == 'record':
        if args.expected_clicks is not None and args.expected_clicks < 0:
            parser.error('--expected-clicks must be nonnegative')
        record(args)
        return 0
    return replay(args)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
