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
make those comparisons unreliable. New runs default to `--pair-policy immediate`:
the first two distinct claps 160–599 ms apart generate one action, with further
claps ignored until a 600 ms quiet gap. `--pair-policy exact-two` retains the
original wait-and-cancel-third behavior for historical comparisons.

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

## DS-CNN comparison and preserved working model

The original 5,114-parameter model and exact five-attempt-tested firmware are
preserved privately, including the float checkpoint, INT8 weights, cutoff,
frontend assets and a SHA-256 manifest. Model files and recordings remain
excluded from Git. A saved image can be uploaded without rebuilding or erasing
the Matter/Thread pairing:

```sh
python3 tools/project.py flash --port /dev/cu.usbmodemXXXX \
  --input-dir .local/deployment/rollback-working-cnn-2ae9802/firmware
```

Six depthwise-separable candidates used the same data, augmentation, frontend,
seed and threshold policy. Two-block variants use 8/16/24 channels, or
12/24/32 for the wider versions. The three-block versions use 12/24/32/32,
with batch normalization folded into the exported integer convolutions.

| Architecture | Keras parameters | INT8 file bytes | Timed diagnostic pairs | False actions on all existing negatives |
| --- | ---: | ---: | ---: | ---: |
| Working standard CNN | 5,114 | 9,944 | 20/22 | 0 |
| DS + Flatten | 1,258 | 8,064 | 18/22 | 1 |
| DS + global average | 922 | 7,736 | 18/22 | 4 |
| Wider DS + Flatten | 2,106 | 9,776 | 19/22 | 1 |
| Wider DS + global average | 1,658 | 9,336 | 18/22 | 1 |
| Three-block DS + BN + Flatten | 3,986 | 14,648 | 18/22 | 0 |
| Three-block DS + BN + global average | 3,538 | 14,216 | 18/22 | 0 |

These are development diagnostics, not a new held-out accuracy estimate.
Negative exposure includes training audio. Fewer parameters did not guarantee
a smaller file: extra graph and quantization metadata increased the three-block
exports. None earned replacement of the working model. The C6 remains on its
original firmware; more negative recordings are required before deploying new
networks. The next collection is described in the recording plan.

`--architecture` also accepts `ds-flat`, `ds-gap`, `ds-wide-flat`, `ds-wide-gap`,
`ds3-bn-flat` and `ds3-bn-gap`. Synthetic export checks cover depthwise kernels,
global pooling, batch-normalization folding and fully integer static graphs.
On-device speedups have **not** been measured for these candidates.

A future timing comparison can be prepared by adding repeated
`cnn_deploy.py --benchmark <experiment>` arguments. It generates a private model
bank; `project.py build --cnn-assets <assets> --cnn-shadow` is required for such
a bank. Use `build --output-dir .local/deployment/experiment-build` to keep
experimental images separate from the default working flash files.
Shadow builds suppress Matter clicks and measure 31 fixed-input
inferences per model before ADC starts, then reload the control model. The
prepared bank compiled, but was not flashed while the device was disconnected
and collection was deferred. Collect the additional negatives and review the
new results before running it on the board.

The original delay after a pair was primarily the 600 ms wait to reject a third
clap, plus completion of the 224 ms post-trigger context. The DS-CNN comparisons
above used that original policy. Reducing network arithmetic alone cannot
remove the deliberate wait.

## Faster first-pair trial and the next data run

The timing-only trial keeps the original working weights and cutoff. It emits
after the second clap's 120 ms association cluster is classified, without the
600 ms third-clap-absence wait. Additional claps in that burst do not cancel or
repeat the action; a 600 ms quiet gap rearms it. The 160–599 ms onset range
already includes 200–300 ms pairs. The 256 ms window and duplicate-window
association still apply, so response is faster rather than instantaneous.

Both policies remain available in firmware builds and host training/evaluation.
For a legacy behavior build, pass `build --pair-policy exact-two`; normal CNN
builds use `immediate`. Legacy experiment reports without a policy field are
interpreted as `exact-two`. Current build manifests record the actual firmware
policy separately from the policy that originally selected the model cutoff.

The original model's full native replay retained 20/22 timed diagnostic pairs
and zero actions in all existing negative recordings under the immediate rule.
Unit tests cover 200/250/300 ms pairs, boundary gaps, one action per longer burst,
and firing after association instead of waiting for quiet.

For the next run, add the new training impacts and background to the existing
training recordings. Keep new held-out impacts/background in a separate
evaluation-only manifest until a model and cutoff have been chosen. Training
now saves an experiment-local manifest snapshot, so deployment and replay use
the exact intended dataset instead of a subsequently edited sibling manifest.

Evaluate a frozen candidate on the new reserved recordings without fitting or
threshold selection:

```sh
.local/cnn-venv/bin/python tools/cnn_lab.py evaluate \
  .local/cnn/experiment-06 .local/cnn/manifest-heldout-20261025.json \
  --pair-policy immediate --output .local/cnn/experiment-06/new-negatives-heldout.json
```

The evaluator verifies the model hash and rejects a `held_out` label for source
recordings already present in the model's experiment. Use `--test-role diagnostic`
when intentionally replaying previously inspected audio. All actual recording
manifests, annotations, outputs and model artifacts remain private.

The additional negative run on 2026-10-25 used nine minutes of new training
impacts, wardrobe doors and background alongside the original training data.
Eight new runs included standard and DS-CNN retrains, training-only hard-negative
refinement, and fine-tuning from the working standard CNN. None improved its
20/22 timed diagnostic pair matches with zero observed negative actions; the
strongest clean challengers matched 19/22. The installed faster-response model
therefore remains unchanged.

Before opening the new reserved recordings, the working model and one
fine-tuned standard-CNN challenger were frozen with their cutoffs. Both produced
zero double-clap actions across three minutes of reserved impact/wardrobe
activity and five minutes of reserved background (475.552 usable seconds).
They accepted 15 and 12 isolated noise transients respectively. These recordings
were not used to tune either model. They now provide a recorded final evaluation
for this comparison; future changes developed using their results need another
fresh evaluation set. This short negative trial does not establish long-term
reliability or add fresh positive recognition evidence.

A subsequent podcast-specific run kept the standard architecture and frontend,
using the previously reserved negatives as additional training data alongside
the new podcast clip. The operator clarified that the initial two seconds were
not consistently quiet. Replay retains those actual starts for noise calibration
and excludes the same initial two seconds from scoring; no silence was inserted.

Before opening a separate podcast excerpt, froze the original model, a refined
fine-tune and a conservative scratch retrain. The conservative option was added
before any predictions on that excerpt because the operator explicitly prefers
fewer false actions even at the cost of missed claps.

| Model | False actions in fresh podcast excerpt | Accepted noise clusters | Previously inspected diagnostic clap pairs |
|---|---:|---:|---:|
| Original working CNN | 1 | 20 | 20/22 |
| Refined fine-tune, same architecture | 0 | 9 | 19/22 |
| Conservative retrain, same architecture | 0 | 1 | 17/22 |

The fresh excerpt supplies 177.776 usable negative seconds. These results favor
trying the conservative option for this preference, while explicitly accepting
lower observed clap recall. Neither replacement has been flashed. All model
cutoffs were fixed before the fresh comparison; its audio is excluded from
training. Longer real-use observation and fresh positive trials remain needed.

## Build and deploy a private model

Use the RMS frontend and a supported architecture. The current working export
is the standard `flat` CNN. The pinned ESP32 core already contains
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
.local/cnn-venv/bin/python tests/cnn_ds_smoke.py
```

The optional smoke checks use synthetic data in memory or a temporary directory
to exercise frontend, training, quantization, held-out replay and C-array
compilation. They make no acoustic accuracy or ESP32-C6 performance claim.

References: [TensorFlow integer quantization](https://github.com/tensorflow/tensorflow/blob/master/tensorflow/lite/g3doc/performance/quantization_spec.md),
[TFLite Micro arena memory](https://github.com/tensorflow/tflite-micro/blob/main/tensorflow/lite/micro/docs/memory_management.md),
and [TensorFlow installation](https://www.tensorflow.org/install/pip).
