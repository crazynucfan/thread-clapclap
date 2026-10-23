# Event-triggered CNN proof of concept

This is an experimental host training workflow and USB waveform-capture
extension. **The board still runs the DSP detector. No trained CNN or embedded
CNN inference runtime is shipped or enabled.** Use at your own risk, with no
commitment to maintenance, support, or updates.

The first step is recording the actual microphone on the installation-room
laptop. Existing FFT feature captures cannot reconstruct mel spectrograms.
All recordings, labels, reports and trained models stay private under `.local/`.
The tools do not upload them. Git ignores `.local/`, audio formats, `.tflite`
files and `ClapModel.local.h`. In-repository data outputs outside `.local/`
are rejected. Keep that directory when transferring data privately between
machines; do not add it to Git.

## Current architecture and targets

The firmware uses Arduino ESP32 **3.3.12** through Arduino CLI, with the
ESP-IDF continuous ADC/DMA API already underneath. An ESP-IDF migration is not
needed to collect data or evaluate a host model.

| Part | Host reference / target |
| --- | --- |
| ADC acquisition | Existing 16 kHz, 12-bit AOUT on D0/GPIO0 |
| CNN sample rate | 8 kHz after low-pass resampling |
| Event window | 256 ms: 32 ms before onset, 224 ms after |
| Spectrogram | 256-point periodic Hann, 128-sample hop, 32 mel bands, 125–3800 Hz |
| CNN input | 15 × 32 × 1 log-power mel features, fixed clipping/scaling |
| Network | Three 3×3 stride-2 convolutions: 8, 16, 24 channels; global average; dense softmax |
| Classes | clap, knock/tap, click/other impact, other/background |
| Export | Full INT8 input, activations, weights and output; INT32 biases |
| Model flash target | Under 80 KiB; synthetic conversion check produced 9,600 bytes |
| Tensor arena target | Under 150 KiB; **not measured or reserved** |
| ESP32-C6 inference target | Under 30 ms if feasible; **not benchmarked** |

The synthetic model size verifies conversion plumbing, not recognition
quality. Normal firmware already uses substantial RAM for Matter/Thread; its
linker headroom is not available tensor-arena space. The USB `MEMORY` command
prints current free, largest contiguous, and minimum free 8-bit heap.

The host reference runs the CNN only on detected transients. A 256 ms window
can overlap another clap 160 ms later. Embedded inference must queue overlapping
windows and preserve onset timestamps while ADC/DMA continues. It must not
stop sampling while waiting for a window or inference. Pair timeout handling
also needs to allow an already-detected clap's classification to finish.

## First capture on a macOS M1 Pro

Use a USB-C **data cable**. The existing four-wire sensor connection is
unchanged. Install Arduino CLI and Python 3.10+ as described in the README.
Use a fresh clone if this laptop still has the history from before the author
rewrite; keep any existing private captures separately.

```sh
python3 tools/project.py setup
python3 tools/project.py build
python3 tools/project.py ports
# Replace this with the actual /dev/cu.usbmodem... port above.
python3 tools/project.py flash --port /dev/cu.usbmodemXXXX

python3 -m venv .local/capture-venv
.local/capture-venv/bin/python -m pip install -r tools/requirements-audio.txt
.local/capture-venv/bin/python tools/wave_lab.py record \
  --port /dev/cu.usbmodemXXXX --duration 30 --label clap \
  --group room-session-A --output .local/cnn/room-A-claps-01.wav
```

Close serial monitors before capture. Wait for **Recording**, keep the first
two seconds quiet, then make five individual claps several seconds apart.
Keep the final second quiet. This initial run checks the microphone and USB
path before spending time on a larger corpus. The script reports ADC range
and near-rail clipping, then closes the port.

```sh
.local/capture-venv/bin/python tools/wave_lab.py inspect \
  .local/cnn/room-A-claps-01.wav
```

This mode records actual room audio to a local mono PCM16 WAV with a JSON
sidecar. It retains the 12-bit ADC exactly as `(ADC - 2048) * 16`; microphone
DC offset remains in the file. Training removes window DC after resampling.
Only explicit waveform-capture commands transmit this audio over USB. Normal
standalone operation retains no waveform recordings.

Waveform capture suppresses Matter clicks and does not require Thread to be
attached. On completion or `WAVE_STOP`, the normal detector recalibrates.
DMA errors abort capture. CRC, session, sequence, sample index, exact duration
and completion checks reject losses. Failed files are marked incomplete and
the training loader rejects them. A SHA-256 detects later WAV changes. Output
files are never overwritten. Native USB carries about 37 KB/s; it does not
use a UART limited to 115200 baud. The board's native USB TX buffer is 4 KiB
and writes remain nonblocking, with losses reported rather than hidden.

## Gather a useful private corpus

Aim initially for 100–200 individual claps and comparable knock/tap and click
examples, plus realistic room background, speech, TV and silence. Capture each
class in its own labelled session and record what actually happened. Keep
gain and placement fixed initially, then cover normal clap distances and
directions. Test single claps, pairs and triples too.

Capture across at least three independent recording groups, for example
`room-session-A` for training, `room-session-B` for validation, and
`room-session-C` for final testing. All recordings from one group must remain
in the same split. Do not split overlapping windows from one recording across
train and test. Keep a long separate no-clap recording for false activations
per hour. A short quiet recording cannot establish a low false-trigger rate.

`--label` is a session hint, not an automatic ground-truth label. Review the
proposed events and any missed claps using the known capture protocol and
local waveform playback. A dataset can otherwise silently learn desk noises
as claps. Choose `other` for normal speech/background; `knock` includes taps.

## Review, train and export locally

Capture alone needs pyserial. For training, use **Python 3.12** and the pinned
training requirements. CPU training works on Apple Silicon; a GPU is optional.
You may instead transfer `.local/cnn/` privately to the workstation. Relative
recording paths in annotation files survive moving the directory.

```sh
python3.12 -m venv .local/cnn-venv
.local/cnn-venv/bin/python -m pip install -r tools/requirements-cnn.txt
.local/cnn-venv/bin/python tools/cnn_lab.py propose \
  .local/cnn/room-A-claps-01.wav --output .local/cnn/room-A-claps-01.labels.json
```

Edit every `unlabelled` event to `clap`, `knock`, `click`, or `other`. Onsets
are milliseconds into the recording, after its two-second calibration. Add
any actual claps the proposal gate missed; they matter for measuring the full
pipeline. Each event needs a complete 32 ms pretrigger / 224 ms posttrigger
window. For a recording confirmed to contain **no claps**, set
`negative_only: true`; this also supplies ordinary background windows and
counts its usable duration as negative exposure.

Copy [the example manifest](../examples/cnn-manifest.example.json) to
`.local/cnn/manifest.json` and point it at reviewed annotation files. The
trainer rejects reused recordings, split recording groups, changed WAVs,
unfinished labels, and incomplete windows. It requires at least 20 training
windows and five validation/test windows per class; these are minimum plumbing
guards, not an adequate accuracy study on their own.

```sh
.local/cnn-venv/bin/python tools/cnn_lab.py train .local/cnn/manifest.json \
  --output .local/cnn/experiment-01
```

Training selects weights on training recordings, early stopping on validation
loss, and the clap threshold on validation non-claps. Quantization calibration
uses training examples from all four classes. Final test predictions do not
select weights or threshold. The private output includes an INT8 `.tflite`,
a C-array header, the frontend specification, and a held-out report.

The report includes thresholded clap recall, a four-class confusion matrix,
and full-recording **host-reference** false double-clap activations per hour.
It also reports a Poisson upper bound when no false event was observed; that
bound assumes stationary events and does not establish room reliability.
Positive pair counts come from reviewed clap annotations. Missing annotations
make those comparisons unreliable. The proposed pair rule requires exactly
two claps 160–599 ms apart, with a third cancelling the sequence.

Do not interpret a high class accuracy with low clap recall, or a threshold
that rejects all claps, as success. Do not repeatedly tune against the final
test recordings; reserve new test groups if you change the design after seeing
those results.

## Deployment gates still outstanding

The exported header is **not linked into the firmware**. Before deploying it:

1. Check microphone recordings for clipping, signal quality and usable class
   separation, then review held-out recognition and negative exposure.
2. Port the specified resampler, trigger, DC removal and log-mel frontend to
   the MCU and verify numerical/input-quantization agreement with host fixtures.
3. Integrate the four required TFLite Micro operators, queue overlapping event
   windows, preserve onset timing, and verify the host pair rule on the board.
4. Measure real tensor-arena usage and heap headroom with Matter/Thread active,
   total firmware flash, worst-case inference time, dropped windows and DMA
   overflows. Reduce model/arena targets if they do not fit.
5. Repeat installation-room positives and continuous negative trials on-device.

These steps require the actual board and real recordings; host model size
does not prove embedded memory or timing. The experiment remains a POC.

## Developer checks

```sh
python3 tools/project.py test
.local/cnn-venv/bin/python tools/cnn_lab.py smoke
.local/cnn-venv/bin/python tests/cnn_training_smoke.py
```

The optional smoke checks use synthetic data in memory or a temporary directory
to exercise frontend, training, quantization, held-out replay and C-array
compilation. They make no acoustic accuracy or ESP32-C6 performance claim.

References: [TensorFlow integer quantization](https://github.com/tensorflow/tensorflow/blob/master/tensorflow/lite/g3doc/performance/quantization_spec.md),
[TFLite Micro arena memory](https://github.com/tensorflow/tflite-micro/blob/main/tensorflow/lite/micro/docs/memory_management.md),
and [TensorFlow installation](https://www.tensorflow.org/install/pip).
