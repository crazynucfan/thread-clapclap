#!/usr/bin/env python3
"""Loss-checked private microphone waveform capture for the CNN experiment."""
import argparse
import array
import hashlib
import json
from pathlib import Path
import secrets
import struct
import sys
import time
import wave
import zlib

ROOT = Path(__file__).resolve().parents[1]
PACKET = struct.Struct('<4sIII64HI')
MAGIC = b'CCW1'
RATE = 16000


def private_path(path):
    path = path.resolve()
    if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / '.local'):
        raise ValueError('Save recordings and derived data under the ignored .local/ directory')
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.text = bytearray()
        self.bad_crc = 0

    def consume_text(self, data):
        self.text.extend(data)
        output = []
        while b'\n' in self.text:
            line, _, self.text = self.text.partition(b'\n')
            line = line.decode('ascii', errors='replace').strip()
            if line.startswith(('WAVE_', 'CAPTURE_ERROR')):
                output.append({'type': 'control', 'line': line})
        if len(self.text) > 4096:
            del self.text[:-256]
        return output

    def feed(self, data):
        self.buffer.extend(data)
        output = []
        while self.buffer:
            position = self.buffer.find(MAGIC)
            if position < 0:
                keep = next((n for n in (3, 2, 1) if self.buffer.endswith(MAGIC[:n])), 0)
                consume = len(self.buffer) - keep
                output.extend(self.consume_text(self.buffer[:consume]))
                del self.buffer[:consume]
                break
            if position:
                output.extend(self.consume_text(self.buffer[:position]))
                del self.buffer[:position]
            if len(self.buffer) < PACKET.size:
                break
            data = bytes(self.buffer[:PACKET.size])
            if zlib.crc32(data[:-4]) != struct.unpack_from('<I', data, PACKET.size - 4)[0]:
                self.bad_crc += 1
                del self.buffer[0]
                continue
            fields = PACKET.unpack(data)
            output.append({'type': 'samples', 'session': fields[1], 'sequence': fields[2],
                           'first_sample': fields[3], 'samples': fields[4:-1]})
            del self.buffer[:PACKET.size]
        return output


class Session:
    def __init__(self, identity, seconds, sink):
        self.identity, self.seconds, self.sink = identity, seconds, sink
        self.packets = 0
        self.begun = self.complete = False
        self.build_id = None
        self.clipped = self.count = 0
        self.low, self.high = 4095, 0

    def accept(self, item):
        if item['type'] == 'control':
            fields = item['line'].split()
            if fields[0] == 'CAPTURE_ERROR':
                raise ValueError(item['line'])
            if len(fields) < 2 or fields[1] != str(self.identity):
                return
            if fields[0] == 'WAVE_ABORT':
                raise ValueError('Firmware aborted waveform capture: ' + ' '.join(fields[2:]))
            if fields[0] == 'WAVE_BEGIN':
                if self.begun or len(fields) != 5 or int(fields[2]) != self.seconds * 250 or int(fields[3]) != RATE:
                    raise ValueError('Invalid waveform start marker')
                self.begun = True
                self.build_id = int(fields[4])
            elif fields[0] == 'WAVE_END':
                if (not self.begun or self.complete or len(fields) != 4
                        or int(fields[2]) != self.seconds * 250 or int(fields[3]) != 0
                        or self.packets != self.seconds * 250):
                    raise ValueError('Waveform capture dropped samples or has an invalid end marker')
                self.complete = True
        elif item['session'] == self.identity:
            if (not self.begun or self.complete or item['sequence'] != self.packets
                    or item['first_sample'] != self.packets * 64 or len(item['samples']) != 64):
                raise ValueError('Missing start marker, missing packet, or sample-clock discontinuity')
            raw = item['samples']
            if any(not 0 <= sample <= 4095 for sample in raw):
                raise ValueError('ADC sample outside 12-bit range')
            self.low = min(self.low, min(raw)); self.high = max(self.high, max(raw))
            self.clipped += sum(sample <= 8 or sample >= 4087 for sample in raw)
            self.count += len(raw)
            pcm = array.array('h', ((sample - 2048) * 16 for sample in raw))
            if sys.byteorder != 'little':
                pcm.byteswap()
            self.sink(pcm.tobytes())
            self.packets += 1


def record(args):
    try:
        import serial
    except ImportError as error:
        raise ValueError('Install pyserial with tools/requirements-audio.txt') from error
    destination = private_path(args.output)
    if destination.suffix.lower() != '.wav':
        raise ValueError('Waveform destination must end in .wav')
    destination.parent.mkdir(parents=True, exist_ok=True)
    sidecar = destination.with_suffix('.wav.json')
    if destination.exists() or sidecar.exists():
        raise ValueError('Capture output already exists; choose a new filename')
    metadata = {'schema': 1, 'complete': False, 'label': args.label, 'group': args.group,
                'sample_rate': RATE, 'duration_s': args.duration, 'channels': 1,
                'encoding': '(ADC12 - 2048) * 16 as signed PCM16', 'actions_suppressed': True}
    identity = secrets.randbelow(0xffffffff) + 1
    decoder = Decoder()
    port = serial.Serial(port=None, baudrate=115200, timeout=0.25, write_timeout=1)
    port.port, port.dtr, port.rts = args.port, False, False
    with destination.open('xb') as raw_file, sidecar.open('x', encoding='utf-8') as metadata_file:
        metadata_file.write(json.dumps(metadata) + '\n'); metadata_file.flush()
        with wave.open(raw_file, 'wb') as writer:
            writer.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            session = Session(identity, args.duration, writer.writeframesraw)
            try:
                port.open()
                port.reset_input_buffer()
                port.write(f'WAVE_CAPTURE {identity} {args.duration}\n'.encode())
                deadline = time.monotonic() + args.duration + 20
                announced = False
                while not session.complete and time.monotonic() < deadline:
                    for item in decoder.feed(port.read(4096)):
                        session.accept(item)
                        if session.begun and not announced:
                            print(f'Recording {args.duration}s locally. Lamp actions suppressed. Keep the first 2s quiet.', flush=True)
                            announced = True
                if not session.complete or decoder.bad_crc:
                    raise ValueError('Incomplete waveform or CRC failure; do not train on this capture')
                metadata.update(complete=True, frames=session.count, detector_build_id=session.build_id,
                                adc_min=session.low, adc_max=session.high,
                                clipped_fraction=session.clipped / session.count)
            finally:
                if port.is_open:
                    if not session.complete:
                        try: port.write(b'WAVE_STOP\n')
                        except serial.SerialException: pass
                    port.close()
                metadata.update(frames=session.count, bad_crc=decoder.bad_crc)
                metadata_file.seek(0); metadata_file.truncate()
                json.dump(metadata, metadata_file, indent=2); metadata_file.write('\n')
        # Hash only after the WAV header has been finalised by close().
    if metadata['complete']:
        metadata['wav_sha256'] = digest(destination)
        sidecar.write_text(json.dumps(metadata, indent=2) + '\n')
        print(f"Saved {session.count} samples; clipped={metadata['clipped_fraction']:.4%}; {destination}")


def load_wave(path):
    metadata = json.loads(path.with_suffix('.wav.json').read_text())
    if (metadata.get('schema') != 1 or metadata.get('complete') is not True
            or metadata.get('wav_sha256') != digest(path)):
        raise ValueError('Waveform is incomplete or has changed since capture')
    with wave.open(str(path), 'rb') as reader:
        if reader.getparams()[:3] != (1, 2, RATE) or reader.getnframes() != metadata['duration_s'] * RATE:
            raise ValueError('Expected complete mono 16 kHz PCM16 waveform')
        samples = array.array('h', reader.readframes(reader.getnframes()))
    if sys.byteorder != 'little': samples.byteswap()
    if len(samples) != metadata['frames']:
        raise ValueError('Truncated WAV sample payload')
    return metadata, samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('record')
    capture.add_argument('--port', required=True)
    capture.add_argument('--duration', type=int, choices=range(4, 601), default=30, metavar='4..600')
    capture.add_argument('--label', choices=('clap', 'non_clap', 'knock', 'click', 'other'), required=True,
                         help='session hint; knock/click/other remain accepted for older workflows')
    capture.add_argument('--group', required=True, help='recording session ID; keep each group in one dataset split')
    capture.add_argument('--output', type=Path, required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('file', type=Path)
    args = parser.parse_args()
    if args.command == 'record': record(args)
    else:
        metadata, _ = load_wave(args.file)
        print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    try: main()
    except (ValueError, TypeError, OSError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr); sys.exit(2)
    except KeyboardInterrupt: sys.exit(130)
