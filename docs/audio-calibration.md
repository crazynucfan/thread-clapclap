# Calibrate with labelled feature captures

The burst classifier is experimental. A passing synthetic fixture or a single
successful pair does not establish reliable recognition. Keep the sensor gain
and placement fixed while gathering a small corpus of positive and negative
trials, then test algorithm changes against separate held-out trials.

## Install and capture

Build/flash using `tools/project.py` as described in the README. Close Arduino
CLI or IDE serial monitors before opening the port with the capture tool.

Normal operation starts with verbose diagnostics off. For interactive debugging,
open a 115200-baud serial monitor and send `LOG ON` with a newline. `LOG OFF`
stops diagnostic formatting and output without stopping recognition or counters.
The setting resets to off on reboot. Startup/error messages remain available,
and feature captures below work independently of this setting.

```sh
python3 -m venv .local/audio-venv
.local/audio-venv/bin/python -m pip install -r tools/requirements-audio.txt
.local/audio-venv/bin/python tools/audio_lab.py record \
  --port /dev/ttyACM0 --duration 15 --label double-clap --expected-clicks 1 \
  --output .local/audio/positive-01.jsonl
```

On Windows use the virtual environment's `Scripts/python.exe` and your COM
port. The capture tool needs pyserial 3.5; replay needs only Python and a C++17
compiler. Outputs are created exclusively so a repeated filename cannot erase
a previous trial.

The board must already be commissioned. Startup waits for Thread attachment.
Wait for **Capturing**. Keep quiet for the first two seconds, clap twice once
in the middle of the window, and keep the final second quiet so the gesture
can finish. Space the claps naturally, about 300 ms apart. The serial start
marker and first feature frame share the detector's reset time; message/reply
timestamps in a chat are not the ground-truth markers.

During capture, recognition runs but **Matter clicks are suppressed**. Live
mode resumes with a fresh two-second calibration afterward. This lets you
measure false triggers without operating household devices. USB is not needed
for standalone detection after calibration.

Each JSONL file contains session metadata, every 8 ms FFT feature frame, and a
completion record with the on-board classifier's detected-pair count. It does
not contain waveform audio, household entity IDs, or credentials. Keep real
captures in ignored `.local/audio/`; publish only deliberately reviewed data.

## Gather negatives and hold out trials

Repeat positive captures with your normal variation in distance, clap strength,
and hand position. Use a new filename each time. For negative sessions use
`--expected-clicks 0`, for example:

```sh
.local/audio-venv/bin/python tools/audio_lab.py record \
  --port /dev/ttyACM0 --duration 30 --label ordinary-speech --expected-clicks 0 \
  --output .local/audio/negative-speech-01.jsonl
```

Include ordinary speech, TV, keyboard/desk taps, a single clap, and three claps.
Label what actually happened. Omit `--expected-clicks` for an unlabelled ambient
observation; it must not be counted as a verified negative. Several distinct
negative sources matter more than repeatedly testing silence.

Use separate training and held-out folders. Choose thresholds on the training
captures; evaluate fresh held-out captures afterward. An exact event-count
match is a session-level check, not proof that each event was correctly aligned
with a physical clap. Longer background trials are needed to estimate unwanted
triggers per hour. Do not infer a long-term rate from a short quiet capture.

## Replay the actual detector

```sh
python3 tools/audio_lab.py replay .local/audio/positive-01.jsonl --decisions
python3 tools/audio_lab.py replay .local/audio/positive-01.jsonl --classifier legacy
python3 tools/audio_lab.py replay .local/audio/held-out/*.jsonl
# Explore a score cutoff without flashing:
python3 tools/audio_lab.py replay .local/audio/training/*.jsonl --min-score 0.75
```

The tool compiles `tools/audio_replay.cpp` against the same header and
`config::audioDetector` settings as the firmware. `legacy` selects the previous
hard spectral gates for comparison. Each result reports detected and expected
counts; a labelled mismatch returns exit status 1. Invalid/incomplete input
returns status 2. No algorithm is reimplemented in Python.

When the embedded detector/config fingerprint matches the local sources and
no override is applied, replay also checks the firmware's recorded click count.
For older sources, legacy replay, parameter overrides, or IDE builds with an
unknown fingerprint, that comparison is reported as unavailable. IDE builds
remain replayable. They do not provide verified source fingerprints.

Do not tune only for positive sessions: a lower cutoff that recognises more
claps may also accept more speech or taps. Compare both sides, then verify new
physical trials with the same gain and placement.

## Integrity and implementation

The board accepts `CAPTURE <session-id> <seconds>` (4–600 seconds) and
`CAPTURE_STOP`. Capture lasts an exact number of 125 Hz feature frames. It
aborts on detected connection changes or ADC stream resets. USB transmission
never waits for space; dropped or partial packets invalidate the capture.

Binary packets use little-endian `<4sIIII7fHHI`, 56 bytes:

| Field | Meaning |
| --- | --- |
| `CCF1` | Protocol/version magic |
| four uint32s | Session, sequence, sample-clock ms, board uptime ms |
| seven floats | RMS, peak, high-band ratio, flatness, concentration, near-rail fraction, positive flux |
| two uint16s | Occupied bins and significant bins whose L1 magnitude more than doubled |
| CRC32 | Standard CRC32 over the preceding 52 bytes |

The host resynchronises around SDK text logs, verifies CRC, requires sequence
zero and consecutive counters, checks 8 ms sample-clock increments including
wraparound, validates feature ranges, and requires a complete end marker with
zero drops. Truncated or corrupt captures are retained as incomplete files for
diagnosis and refused by replay. Capture changes neither gain nor persistent
Matter commissioning data.

Background reading: [aubio's spectral onset descriptors](https://aubio.org/doc/latest/specdesc_8h.html)
cover spectral-flux and high-frequency onset methods. This project implements
its own fixed-point L1 flux descriptor; that reference does not validate these
clap scores or thresholds.
