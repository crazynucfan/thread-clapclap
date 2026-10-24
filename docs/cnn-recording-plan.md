# Installation-room CNN recording plan

Collect real audio from the Waveshare microphone through the XIAO, using
[`wave_lab.py`](../tools/wave_lab.py). Phone or laptop microphones have different
acoustics and are not a substitute for this training corpus. Setup, commands,
annotation format and deployment requirements are in the
[CNN experiment guide](cnn-experiment.md).

The CNN classifies **individual sound events**. The separate sequence rule
requires exactly two recognised claps 160–599 ms apart. Record single claps,
pairs and triples: a single or triple contains positive *clap* examples even
though it must not activate the double-clap action.

## Before collecting the corpus

1. Flash the waveform-capable firmware. The earlier quiet-logging firmware
   cannot record raw audio; enabling diagnostic logs does not add that feature.
2. Close the serial monitor. Record a 30-second pilot with two seconds quiet,
   five individual claps several seconds apart, and a quiet final second.
3. Inspect capture integrity, clipping and the individual clap waveforms before
   spending time on a larger dataset. A small overall clipping fraction can hide
   a badly clipped clap in an otherwise quiet recording. A CNN cannot recover
   detail lost to saturation.
4. Settle on a usable gain and leave it fixed for the first corpus. Record gain
   position, microphone placement, approximate distance and sound types in
   private session notes under `.local/cnn/`. If gain changes materially later,
   collect fresh evaluation sessions at the new setting.

Capture retains actual room audio locally. Files, notes, annotations and models
stay under ignored `.local/`. No audio is uploaded by these tools. Waveform
capture suppresses Matter clicks while recording.

## Structured recordings

Use short recordings of one known sound family at a time. Leave several
seconds between individual examples so their context and identity are clear.
Keep the first two seconds and final second quiet in every file. Review actual
onsets afterward; a planned action time is not an exact acoustic label.

| Label | Include | Initial collection target across all splits |
| --- | --- | --- |
| `clap` | Ordinary softer and louder claps, intended near/far distances, different directions and hand positions; later include other household users | About 160 individual claps |
| `knock` | Putting cups, phones, books and other objects down; tapping/knocking tables, doors and furniture | About 100 impacts across several objects and surfaces |
| `click` | Light switches on/off, plugs/buttons, keys and other short mechanical clicks | About 100 events across relevant devices |
| `other` | Speech, TV/music, footsteps, doors/drawers, fabric movement and ordinary quiet room background | Several short sessions plus longer background recordings |

These counts are starting collection goals, not a promise of accuracy. The
trainer's lower per-class minimums only check that the pipeline has enough data
to run. Diversity matters more than repeating an identical sound hundreds of
times. A door slam or ambiguous impact can be labelled `other`; use consistent
labels and ensure it is not accidentally labelled `clap`.

For claps, start with isolated events, then include natural pairs and triples.
Keep ordinary variation rather than requiring every clap to sound identical.
Record some positives over expected room background too. Later, deliberately
try confusing negative sequences: two objects placed down, a switch followed
by a tap, or two clicks roughly 200–500 ms apart. These exercise the complete
double-clap path, not just isolated classification.

## Longer no-clap recordings

Collect both ordinary room activity and deliberate likely misfires. For the
deliberate material, separate recordings for objects, switches and other sound
families make event review easier. For the natural background, allow realistic
speech, TV and household movement, with **no intentional claps**.

The current recorder accepts 4–600 seconds per file. Build longer exposure from
several files, keeping the same recording-group ID for files from the same
session. Account only for recorded usable time: pauses between files are not
observed exposure, and pairs crossing those gaps cannot be evaluated.

Run `cnn_lab.py propose` on each WAV. It proposes 256 ms windows around
transients, with 32 ms of pretrigger context. Review each proposed label, and
add any real claps the trigger missed. This is essential: a CNN cannot classify
a clap that its event trigger never supplies.

For a recording verified to contain no claps, set `negative_only: true` and
finish reviewing the proposed events. The existing loader also samples ordinary
background windows from these files. Do not turn every overlapping slice of
silence into an example; that mainly duplicates easy background and can
overwhelm the useful impacts. A mixed recording that contains a clap must not
be marked negative-only.

## Split sessions before extracting windows

Assign whole recording groups to splits before training. A practical initial
allocation is:

| Split | Example group | Clap / knock / click targets | Background |
| --- | --- | --- | --- |
| Training | `room-session-A` | 100 / 60 / 60 | About 10 minutes, including deliberate misfires |
| Validation | `room-session-B` | 30 / 20 / 20 | About 5 minutes with fresh repetitions |
| Final test | `room-session-C` | 30 / 20 / 20 | Aim for 30–60 minutes of fresh no-clap exposure |

Use genuinely separate sessions, preferably at different times; changing a
group name halfway through one recording does not make it independent. Every
split needs examples of every class. Increase the number of independent groups
as the corpus grows. All crops, overlapping windows and augmented variants of
a source recording stay in that source's split. A single long recording diced
randomly into all three splits would make the evaluation overly optimistic.
This follows the general rule to
[keep test data out of model selection](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).

Use validation recordings for threshold and model choices. Keep final-test
recordings untouched until evaluating the chosen model. If a failed final-test
example becomes training material during the next iteration, reserve new test
sessions. Mine training/validation false positives into additional reviewed
negative examples; do not silently train on the final test set.

## What to measure

Report individual clap recall and confusion with each non-clap class, then
replay complete held-out recordings through the **trigger, CNN and pair rule**.
Include missed double claps, single/triple behaviour, and false double-clap
activations per recorded hour. Aggregate pair counts alone cannot identify
which specific attempts were missed or falsely accepted; review event timing.

Thirty to sixty minutes is an initial test, not evidence of a very low long-term
false-trigger rate. More independent background exposure is needed as the
prototype improves. Host results also do not establish embedded reliability:
MCU frontend parity, inference queues, memory, latency and audio-overflow checks
remain required before replacing the deployed detector.
