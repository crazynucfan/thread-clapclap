# Event-triggered CNN proof of concept

This is an experimental training, waveform-capture and opt-in device inference
workflow. **The development board runs a private CNN build. Normal builds use
DSP, and no trained model weights are shipped in this repository.** Use at your own risk, with no
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
| Proposal gate | 700 Hz high-pass, 2 ms RMS, adaptive noise threshold, 32 ms cooldown |
| Spectrogram | 256-point periodic Hann, 128-sample hop, 32 mel bands, 125–3800 Hz |
| CNN input | 15 × 32 × 1 log-power mel features; waveform RMS normalized before the log floor |
| Network | Three 3×3 stride-2 convolutions: 8, 16, 24 channels; flatten; dense softmax; 5,114 parameters |
| Classes | clap, non-clap (all other sounds) |
| Export | Full INT8 input, activations, weights and output; INT32 biases |
| Model flash target | Under 80 KiB; improved real-audio export is 9,944 bytes |
| Tensor arena | 8 KiB reserved; 3,052 bytes used by the current model |
| ESP32-C6 processing | Typically about 20 ms frontend + 6 ms inference; wall-time peaks include Thread task preemption |

Model file size does not establish embedded resource usage. Normal firmware already uses substantial RAM for Matter/Thread; its
linker headroom is not available tensor-arena space. The USB `MEMORY` command
prints current free, largest contiguous, and minimum free 8-bit heap.

Both the host reference and device run the CNN only on detected transients. A 256 ms window
can overlap another clap 160 ms later. Embedded inference must queue overlapping
windows and preserve onset timestamps while ADC/DMA continues. It must not
stop sampling while waiting for a window or inference. Pair timeout handling
also needs to allow an already-detected clap's classification to finish.

The proposal cooldown is deliberately shorter than the pair rule's 160 ms
minimum: rejecting one transient must not suppress the following clap. Accepted
windows are grouped within 120 ms of the first candidate, without extending that
cluster indefinitely. The strongest 1 kHz high-band attack in the following
32 ms supplies its timestamp; CNN score breaks ties. An early window can see a
future clap and score as highly as the true attack. Choosing by score alone
can count that clap twice and cancel a valid pair. The more sensitive gate also
increases candidate load. The device uses a 16-candidate queue and a
1.024-second sample ring. It drains ADC data before processing one ready
candidate, and discards the gesture on queue/ring overflow rather than using
stale audio. The classifier watermark prevents the pair timer from firing
before a possible third clap has been classified.

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

Follow the [installation-room recording plan](cnn-recording-plan.md) for sound
families, collection targets, independent recording groups and longer no-clap
tests. It distinguishes training negatives from reserved evaluation exposure.

Aim initially for 100–200 individual claps and comparable knock/tap and click
examples, plus realistic room background, speech, TV and silence. Capture claps
separately from confirmed no-clap sessions and record what actually happened. Keep
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
as claps. Use `non_clap` for impacts, switches, rattles, speech and background.
A verified no-clap recording may mix all these sounds, including impacts over
fan hum. The binary model only decides whether an event is a clap. Older
`knock`, `click` and `other` annotations are accepted and mapped to `non_clap`.

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

Edit every `unlabelled` event to `clap` or `non_clap`. Onsets
are milliseconds into the recording, after its two-second calibration. Add
any actual claps the proposal gate missed; they matter for measuring the full
pipeline. Each event needs a complete 32 ms pretrigger / 224 ms posttrigger
window. For a recording confirmed to contain **no claps**, set
`negative_only: true` and label its proposed events `non_clap`; this also
supplies ordinary background windows and counts its usable duration as negative
exposure. The capture hint alone does not verify the absence of claps.

Copy [the example manifest](../examples/cnn-manifest.example.json) to
`.local/cnn/manifest.json` and point it at reviewed annotation files. The
trainer rejects reused recordings, split recording groups, changed WAVs,
unfinished labels, and incomplete windows. It requires at least 20 training
windows and five validation/test windows per class; these are minimum plumbing
guards, not an adequate accuracy study on their own.

```sh
.local/cnn-venv/bin/python tools/cnn_lab.py train .local/cnn/manifest.json \
  --output .local/cnn/experiment-01 --epochs 100
```

Training adds gain, onset jitter, background mixes at 0–30 dB SNR, spectral
balance, speed and short reflection variants using training recordings only.
Background mixing and additional hard negatives use verified no-clap training
files. Stronger 1 kHz proposals supply the extra training windows; ordinary
background windows already cover weak noise. Evaluation audio remains untouched.
The augmented examples are variants of the original reviewed sounds, not new
independent recordings.

Both training and validation loss balance the two classes. Early stopping
restores the best validation-loss weights. At export the batch dimension is
fixed to one, avoiding dynamic shape operators; all tensors remain integer.
The default waveform frontend scales each DC-removed window to RMS 0.1
(denominator floor 1e-5) before computing log mel. This prevents quiet claps
from collapsing against the log floor. Log mel is clipped to [-16,4] and scaled
to [0,1]. Optional `--frontend preemphasis|contrast` and `--architecture wide`
support explicit experiments; the successful default uses `rms` and `flat`.

Threshold selection maximises one-to-one full-stream validation clap recall,
then reviewed-window recall, with zero unmatched double-clap actions over a
0.5–1.0 cutoff grid. It checks positive validation recordings too, so isolated
claps must not produce actions. Pair matches require both onsets within 100 ms
of the annotations. Equal-recall choices prefer fewer isolated false clap
classifications, then a higher cutoff. A verified negative-only validation
recording is required; isolated negative windows cannot measure the pair rule.

An isolated high-scoring impact does not automatically raise the cutoff above
nearly every real clap. The report retains counts of isolated false clap windows
and trigger candidates, alongside false actions and observed negative duration.
With an exactly-two-clap rule, changing the cutoff can also change third-clap
cancellation; false-pair counts need not vary monotonically. Zero false actions
in a short validation session does not establish a reliable false-trigger rate.

Quantization calibration
uses training examples from both classes. Final test predictions do not
select weights or threshold. The private output includes an INT8 `.tflite`,
a C-array header, the frontend specification, float checkpoint, training history,
and evaluation report. `selection-frozen.json` saves the model, data and trainer
hashes, gate coefficients and chosen cutoff before any test predictions.
`replay.json` retains candidate scores, associated timestamps and one-to-one
clap/pair matches. No timestamp can explain multiple ground-truth events.

The report includes thresholded clap recall, a two-class confusion matrix,
and full-recording **host-reference** false double-clap activations per hour.
It also reports a Poisson upper bound when no false event was observed; that
bound assumes stationary events and does not establish room reliability.
Positive pair counts come from reviewed clap annotations. Missing annotations
make those comparisons unreliable. The proposed pair rule requires exactly
two claps 160–599 ms apart, with a third cancelling the sequence.

Do not interpret a high class accuracy with low clap recall, or a threshold
that rejects all claps, as success. Do not repeatedly tune against the final
test recordings; reserve new test groups if you change the design after seeing
those results. When rerunning against already-inspected recordings during
development, pass `--test-role diagnostic`; this is recorded in the report.
Positive-trial reports also retain annotation review notes and unresolved
ground-truth limitations.

## Improvement using the existing room recordings

The improved default was trained on the same group A recordings, selected with
group B, and replayed on previously inspected group C. No extra recordings or
model-generated labels were added. Group C failures informed development, so
these results are **diagnostic**, not a fresh estimate of unseen accuracy.

| Existing-recording result | Previous model/pipeline | Improved model/pipeline |
| --- | ---: | ---: |
| Validation reviewed clap recall | 35/38 | 38/38 |
| Diagnostic reviewed clap recall | 36/45 | 42/45 |
| Diagnostic timed double-clap matches | 9/22 | 20/22 |
| Unmatched diagnostic pair detections | 0 | 0 |
| Diagnostic background false actions | 0 in 297.776 s | 0 in 297.776 s |
| Model bytes | 9,496 | 9,944 |

The improved model used 100 original reviewed training claps and 1,079 non-clap
windows, expanded to 2,170 and 7,637 training variants/windows. The validation
cutoff was 0.76171875, with two isolated accepted background candidates and no
validation actions. All four verified negative recordings produced zero
actions over 1,131.104 usable seconds (18.85 minutes); 715.552 seconds of that
audio was used in training, so this combined number is not independent evidence.
The single-clap validation recording also produced no actions.

Two clearly timed pairs remain missed, beginning at 23.010 s and 44.868 s in the
diagnostic pair recording. Their first claps have unusual spectral balance or
masking. One further operator-confirmed pair still has an unresolved second
onset and is excluded from the 22 timed comparisons. Labels were preserved.
The complete laptop run took about 40 seconds; workstation training was not
needed. This model was subsequently integrated into an opt-in device build.

## Build and deploy a private model

Use the current RMS/flat export. The pinned ESP32 core already contains
TensorFlow Lite Micro and ESP-NN; no extra Arduino library is required.
Generate assets using the isolated training environment:

```sh
.local/cnn-venv/bin/python tools/cnn_deploy.py .local/cnn/experiment-05 \
  --output .local/cnn/device-assets
.local/cnn-venv/bin/python tools/cnn_embedded_check.py .local/cnn/experiment-05 \
  --assets .local/cnn/device-assets --output .local/cnn/embedded-parity.json
python3 tools/project.py build --cnn-assets .local/cnn/device-assets
python3 tools/project.py flash --port /dev/cu.usbmodemXXXX
```

Replace the experiment and USB port with your own. If using an isolated Arduino
CLI, pass its `--cli` and `--cli-config` options before `build` or `flash`.
The build copies source plus private assets into ignored `.build/` staging;
it never adds the model to tracked firmware files. Build metadata records the
model and firmware hashes. A normal `build` without `--cnn-assets` produces
DSP firmware. Uploading preserves the existing Matter/Thread credentials.
Keep the previous app binary if you need a rollback; do not erase NVS.

The embedded frontend uses the same centered Kaiser-5 resampler, with 20 input
samples of delivery delay while preserving onset timestamps. Q30 FIR/Hann and
twiddle coefficients, Q24 accumulation/FFT, sparse mel weights and precomputed
INT8 log-mel boundaries avoid expensive software floating-point work. Replay of
all existing negative recordings and validation/diagnostic positives preserved
every candidate timestamp and classifier decision. Input codes differed by at
most one step; complete pair results were unchanged, including 20/22 timed
diagnostic matches and zero false actions in existing negative recordings.

At boot, frontend and inference fixtures must pass before sampling/actions are
enabled. Inference starts after Thread connects, followed by two seconds of
noise calibration. Connection changes, capture completion and audio faults
discard pending gestures. ADC/DMA remains active during inference. Normal
diagnostics stay off, including when powered without a USB host.

Send `CNN STATUS` over USB for the active model hash, inference/frontend time,
arena usage, heap, queue depth/drops and DMA overflows. `LOG ON` prints candidate
scores and Matter events; `LOG OFF` disables it. `MEMORY` reports heap headroom.
`WAVE_CAPTURE` still suppresses actions. Legacy `CAPTURE` evaluates the DSP
feature path and retains its existing replay fingerprint, even in a CNN build.

The current build uses approximately 2.783 MB of the 3 MiB app partition and
197 KB of static RAM. Live checks measured about 99 KB free heap, with an
84.8 KB minimum, while commissioned and connected as a Thread router. Dense
background processing completed with zero queue drops and zero DMA overflows
in the observed run. These measurements are specific to this model/core/build;
repeat them after changing the architecture or frontend. Fresh on-device
positive and continuous negative trials remain necessary for reliability claims.

## Developer checks

```sh
python3 tools/project.py test
.local/cnn-venv/bin/python tools/cnn_lab.py smoke
.local/cnn-venv/bin/python tests/cnn_training_smoke.py
.local/cnn-venv/bin/python tests/cnn_frontend_smoke.py
```

The optional smoke checks use synthetic data in memory or a temporary directory
to exercise frontend, training, quantization, held-out replay and C-array
compilation. They make no acoustic accuracy or ESP32-C6 performance claim.

References: [TensorFlow integer quantization](https://github.com/tensorflow/tensorflow/blob/master/tensorflow/lite/g3doc/performance/quantization_spec.md),
[TFLite Micro arena memory](https://github.com/tensorflow/tflite-micro/blob/main/tensorflow/lite/micro/docs/memory_management.md),
and [TensorFlow installation](https://www.tensorflow.org/install/pip).
