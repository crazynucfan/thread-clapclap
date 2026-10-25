# Hardware validation

These checks require the physical XIAO, sound sensor, and the user's Home
Assistant/Thread network. Digital-mode flashing, startup, commissioning, router
promotion, and the lamp automation's action have been checked. Analog sampling
has also been checked; acoustic accuracy testing is in progress. Record the firmware commit, Home Assistant
version, Border Router, sensor settings, and outcome when performing them.
Do not include Thread credentials or device storage.

- [ ] Wiring matches DOUT → D1/GPIO1, VCC → 3V3, GND → GND.
- [x] USB upload succeeds; normal boot shows a Generic Switch endpoint and pairing code.
- [ ] The device starts on USB power without a connected serial monitor.
- [ ] DOUT is normally HIGH; one nearby clap produces one accepted burst.
- [ ] Single clap produces no Home Assistant action.
- [x] Two double-clap sequences reach the Matter click API in the serial log.
- [x] A recognised physical double clap produces one lamp action after the recognition window.
- [ ] Triple clap produces no action.
- [ ] Quiet room, conversation, TV, and a door closing are evaluated for false triggers.
- [x] Commissioning into Home Assistant joins the existing Thread network.
- [x] The Generic Switch event entity appears and updates in Home Assistant.
- [x] Serial output reports router eligibility and the actual Thread role.
- [x] Thread automatically promotes the attached board from child to router.
- [x] AOUT → D0 streams analog samples while Matter remains paired and attached.
- [x] Fixed-point FFT processing runs without ADC DMA overflow in observed runs.
- [x] Lamp-test automation is installed and enabled.
- [x] Its action toggles the existing lamp group off → on → off when triggered manually.
- [ ] Unplug/replug retains pairing and the automation still works.
- [ ] Events during a network outage are discarded and not replayed on reconnect.
- [ ] Holding BOOT for five seconds resets pairing and allows commissioning again.

## Results

### 2026-10-21: first flash and boot

- Firmware: `de860cf`; Arduino ESP32 3.3.12.
- Hardware: XIAO ESP32-C6FH4, silicon revision v0.2, 4 MB flash.
- Upload: successful over USB Serial/JTAG through usbipd-win and WSL;
  esptool verified the hashes of the bootloader, partition table, and application.
- Startup: Generic Switch endpoint 1 and a manual commissioning code printed.
- OpenThread: `role=disabled router_eligible=yes config_error=0`, as expected
  before commissioning. Actual network attachment and router promotion remain
  untested.
- Sensor input: sampled HIGH at idle, no queue overflows observed, and at least
  one sound burst accepted by the detector. A controlled double-clap test and
  sensitivity tuning remain pending.
- Home Assistant commissioning and automation delivery: pending.

### 2026-10-21: commissioning and first double-clap recognition

- The user confirmed pairing and connection in Home Assistant.
- Serial output independently confirmed `commissioned=yes connected=yes` and
  `role=child router_eligible=yes config_error=0`. It initially attached as a
  child; subsequent router promotion is recorded below.
- Two sequences reached the firmware's `Double clap -> Matter button click`
  log, each following two accepted bursts. This verifies local recognition
  and submission to the SDK, not delivery to the Home Assistant event entity.
- Other recorded sequences contained three or more accepted bursts and were
  rejected. Their physical source was not established; controlled tuning and
  false-trigger testing remain pending.
- Zero sound queue overflows were reported.
- Transient operational-advertising errors occurred during commissioning,
  before attachment completed. Subsequent status messages showed the device
  attached; reconnect behaviour and event delivery still need checking.
- Home Assistant version, Border Router model, sensor adjustment positions,
  event entity updates, and automation action results have not been recorded.

### 2026-10-21: router promotion

- After initially attaching as a child, the device automatically changed to
  `role=router router_eligible=yes config_error=0`.
- Four consecutive ten-second status reports showed the router role with
  `commissioned=yes connected=yes` and zero sound queue overflows.
- This confirms router-role promotion on the existing Thread network.
  Traffic-forwarding and coverage measurements have not been performed.

### 2026-10-21: analog FFT filtering and lamp action

- The user added AOUT → D0/GPIO0 using the Waveshare lead's blue wire, keeping
  DOUT → D1, 3V3, and GND connected.
- The first floating-point implementation overran the ADC buffer. It was
  replaced by a fixed-point high-pass filter, Hann window, radix-2 FFT, and
  integer lookup for per-bin logarithms. USB logging is non-blocking.
- The optimized stream ran for more than ten minutes with zero DMA overflows,
  about 250-251 overlapping frames per two-second status interval, and
  `commissioned=yes connected=yes role=router`. A later startup showed task
  preemption in the maximum batch time while the DMA buffer absorbed it.
- Input bias was around 1,600 raw ADC counts and quiet RMS around 6-7 counts;
  observed clipping fraction was zero. Counts are not calibrated voltages.
- A controlled double-clap attempt was captured with peak RMS around 606 and
  500 counts, but the initial duration check rejected both bursts. This led
  to peak-relative release, echo-tail lockout, and module-appropriate spectral
  bounds. No gain adjustment was needed for that captured attempt.
- Thirty-one host tests pass: 14 digital tests and 17 analog tests, including
  reverberant tails and a feature fixture based on the captured clap metrics.
  Undefined-behaviour sanitizer checks passed on the analog tests.
- An enabled local Home Assistant automation connects the button InitialPress
  event to a lamp-group toggle. A generic version with placeholder entity IDs
  is in `home-assistant/examples/toggle-lamps.yaml`. The deployed household
  configuration is kept private.
- Manually triggering the automation twice changed the group off → on → off.
  This verifies the action path, not acoustic detection or event delivery.
  No credentials or real household entity IDs are included in this repository.
- End-to-end clap → Matter event → lamp toggle and real-room false-positive
  rates remain under test.

### 2026-10-21: end-to-end event and timing refinement

- A physical pair was accepted with peak RMS about 695 and 726 counts,
  high-band fractions 0.35 and 0.33, flatness 0.14, and zero clipping.
- The serial log emitted one Matter click. The Home Assistant event entity
  updated, the automation's `last_triggered` changed once, and the test
  lamp group changed from off to on. ShortRelease did not cause a second toggle.
- Earlier acoustic attempts were frequently rejected, so one successful event
  does not establish reliable recognition. Logs exposed a duration boundary
  bug: waiting for release quiet was incorrectly counted toward the maximum
  burst time. A regression test now covers that case.
- The latest flashed version excludes release-confirmation time, uses a 25%
  peak-relative release level, and sets minimum spectral flatness to 0.08.
  It remains paired, attached as a router, and reports zero DMA overflows.
- Repeatability, single/triple-clap checks, and labelled real-room negative
  tests remain to be completed. No measured precision/recall is claimed.

### 2026-10-21: repeated trials and sequence regressions

- The operator reported one visible success among four pairs, followed by no
  visible successes in another three-pair trial. Recognition remains unreliable.
- Logs showed a weak precursor consuming a subsequent strong clap, and a long
  narrowband interruption clearing an otherwise accepted pair. Sequence handling
  now ignores much weaker interruptions, restarts on a stronger/different
  candidate, and preserves pending claps across rejected tonal sounds.
- Six additional feature-timeline regressions cover those cases, including
  preservation of sustained-broadband and comparable-triple rejection. All 37
  host tests and the analog undefined-behaviour sanitizer check pass.
- Closely delivered Matter events were seen in Home Assistant. The supplied
  automations and the deployed test automation now queue up to ten runs. This
  prevents overlapping actions being dropped; it does not correct delayed
  delivery or establish that each event corresponds to an intentional clap.
- Added candidate onset times, click sequence numbers, and Thread neighbour/
  MAC acknowledgement/retry diagnostics. No raw audio is recorded or sent.
- The sequence update compiled and flashed without erasing commissioning. The
  board reattached as a router and reported zero audio DMA overflows. Later
  Home Assistant history contained event and group-state changes, but these
  cannot be confidently assigned to the operator's individual attempts.
- Wiring artwork now explicitly shows the labelled underside with USB at the
  top: D0/D1 on the right and GND/3V3 on the left. Connections are unchanged.

- A subsequent diagnostic build adds the peak/RMS crest factor and significant
  FFT-bin count to candidate logs without relaxing spectral thresholds. Tone
  and broadband FFT fixtures also check those bin counts. This build was
  flashed with commissioning retained; the board reattached as a router and
  continued sampling without DMA overflows. At that stage, a labelled physical pair was still
  needed to determine the next spectral-classifier adjustment.

### 2026-10-21: labelled spectral-flatness miss

- A requested physical double clap produced two strong candidates 296ms apart.
  The first measured RMS 482.8, high-band ratio 0.21, flatness 0.16,
  concentration 0.29, 26 significant bins, and crest factor 3.04. The second
  measured RMS 425.3, high-band ratio 0.16, flatness 0.03, concentration 0.36,
  23 significant bins, and crest factor 3.83. Both had zero near-rail clipping.
- The first candidate passed; the second failed only the 0.08 flatness gate.
  No Matter click was emitted and no new Home Assistant event appeared.
- The classifier now admits a strong low-flatness supporting candidate when
  it occupies at least 20 significant bins, exceeds both 150 RMS counts and
  12 times ambient noise, and has concentration at most 0.40. The other
  high-band, crest, clipping, attack, duration, and pair-consistency checks
  still apply. At least one burst in the pair must pass the strict spectral
  check, in either order; two supporting candidates alone cannot trigger.
- Eight new feature-timeline tests cover the measured pair in both orders,
  rejection without a strict anchor, weak/narrow/low-SNR support, and clearing
  strict evidence after a restart or timeout. All 45 host tests and the analog
  undefined-behaviour sanitizer check pass. These fixtures reconstruct feature
  bounds; they are not raw audio recordings or measured real-room accuracy.
- The firmware compiles with Arduino ESP32 3.3.12: 2,684,664 bytes of flash and
  143,644 bytes of static RAM. Upload succeeded, commissioning was retained,
  the board reattached as a router, and audio DMA overflows remained zero.
  The controller then confirmed a fresh secure session and successful event
  subscription after automatic recovery from the reboot. End-to-end physical
  retesting is pending.

- During the post-upload observation, the configured test lamp group and all
  five member plugs were unavailable in Home Assistant. The controller logged
  connection timeouts for the Wi-Fi plugs before this firmware upload. The
  clapper's own Matter subscription recovered successfully. A fresh physical
  pair can test recognition and button delivery, but visible lamp validation
  is pending restoration of the target plugs. No reliability improvement is
  claimed from the feature regression alone.

### 2026-10-21: post-update physical trial still missed

- The operator confirmed that the test lamp plugs had intentionally been
  powered off. Their unavailability is explained and is separate from clap
  recognition and Matter button delivery.
- The subsequent requested physical trial produced no double-clap output in
  the firmware log and no new button event in Home Assistant. Strong bursts
  were present, but some failed the remaining high-band, flatness, spread, or
  concentration checks. Accepted individual candidates did not complete a
  gesture. Several bursts were logged near the report; their exact assignment
  to physical attempts was not independently timestamped.
- The pair-level regression fix therefore has not established reliable
  physical recognition. Further calibration against labelled real-room
  positive and negative examples is required; passing synthetic tests is
  insufficient evidence of detector accuracy. No classifier thresholds were
  changed during this verification.

### 2026-10-21: attack-window scoring and feature capture

- The user chose to continue clap detection with the existing hardware.
- The default analog classifier now combines positive FFT flux, the number of
  rising significant bins, amplitude-weighted spectral evidence across an
  attack window, rise time, and the burst's early-energy fraction. The previous
  single loudest-frame flatness/high-band vetoes are replaced by a weighted
  score, while clipping, crest, duration, echo, triple, and pair checks remain.
- A weak broadband precursor's spectral/flux evidence is attenuated as a later
  peak grows. Signal fixtures cover both rejection of a weak precursor before
  a tonal burst and acceptance of a strong broadband attack followed by ringing.
- Added complete feature capture over USB and replay using the same C++ detector
  and configuration as firmware. Captures suppress Matter actions, retain no
  waveform audio, and stay in the ignored local directory. CRC, sequence,
  sample-clock, and completion checks reject lossy data. Source/configuration
  fingerprints permit live-versus-replay click-count comparisons.
- All 73 automated checks pass (45 retained legacy, 17 burst/protocol, eleven
  Python capture/replay), and the new native suite passes undefined-behaviour
  sanitization. The build uses 2,687,452 bytes of flash and 144,276 bytes of
  static RAM, leaving 183,404 bytes of static allocation headroom. Runtime heap
  availability is separate from that linker figure.
- Upload succeeded without erasing commissioning. The first instrumentation
  run reattached as a router and reported zero DMA overflows. A capture attempt
  interrupted by a link change was correctly marked incomplete. Capture start
  now waits for Thread attachment and still invalidates a subsequent link loss.
- These engineering weights are not a trained confidence model. Labelled
  physical positive and negative sessions and held-out comparisons remain
  necessary; no recognition rate is claimed from synthetic checks.

- A final ten-second unlabelled ambient capture contained all 1,250 frames,
  consecutive sequence numbers, valid CRCs, and 8ms sample-clock increments.
  The source/configuration fingerprint matched. Replaying the same C++ detector
  returned the on-board count exactly (zero pairs). Legacy replay also returned
  zero. This verifies the instrumentation path, not a false-positive rate:
  the ambient sounds were not labelled. Real data stays ignored and private.

- After the final capture, live diagnostics again showed retained commissioning,
  router role, zero DMA overflows, and a maximum processed-batch time of 3,922us
  over that observation. This is an observed maximum, not a real-time guarantee.

### 2026-10-21: labelled positive and interference captures

- A controlled 60-second capture contained one requested double clap. All
  7,500 frames passed integrity checks. The on-board burst detector reported
  one pair and native replay agreed exactly. The prior hard-gated detector
  reported zero pairs on the same frames.
- The two accepted bursts scored 0.984 and 0.925. The second peak frame had
  high-band ratio about 0.116, flatness about 0.053, and 17 significant bins;
  the burst detector used earlier attack evidence to retain the clap. These
  scores are weighted features, not probabilities or an accuracy estimate.
- A separate controlled 60-second no-clap session included ordinary speech
  and keyboard/desk taps. Both detectors produced one false pair. All 7,500
  frames were complete and replay matched the on-board count. Matter actions
  were suppressed throughout both captures, so no lamp action or delivered
  Home Assistant event is claimed from this test.
- The operator considers typing the likely source of the false trigger and
  confirmed that typing will not occur in the installation room. The capture
  does not independently distinguish speech from individual taps. It remains
  a useful stress negative, not a measurement of expected room background.
- No score threshold was changed to fit these two sessions. Installation-room
  tests should cover normal clap distances and relevant background sounds.
  One successful positive plus one failed stress negative does not establish
  repeatability or a false-trigger rate. Real feature captures remain private
  under the ignored local directory.

### 2026-10-21: live delivery after capture validation

- A subsequent live double clap was accepted with onsets 240ms apart, peak
  RMS 275.6 and 542.2, and burst scores 0.828 and 0.817 (cutoff 0.70).
- The board emitted a Matter click and Home Assistant recorded its InitialPress
  and ShortRelease. This verifies live button delivery for this trial; the lamp
  group was still unavailable, so no physical lamp toggle is claimed.
- The device remained commissioned, attached as a router, and reported zero
  DMA overflows. The sensor's physical placement was not independently verified.
- These valid scores are lower than the first labelled positive pair. Retaining
  the cutoff avoids rejecting this trial solely to fit the earlier typing stress
  negative. Repeated installation-room positives and background tests remain
  necessary before claiming reliable recognition.

### 2026-10-21: quiet operation for installation-room testing

- Verbose detector, periodic audio, Matter, and Thread diagnostics now default
  to off after reboot. Disabled diagnostics skip formatting as well as USB
  writes. Recognition, counters, router configuration, and capture remain active.
- `LOG ON` and `LOG OFF`, each terminated by a newline, toggle diagnostics at
  runtime. Startup information, errors, and requested capture output remain.
- All 73 automated checks passed. The pinned ESP32-C6 build compiled and was
  flashed without erasing commissioning data.
- Hardware checks observed no periodic diagnostics for 12 seconds before
  `LOG ON`, then verified audio/Matter/Thread output, retained counters, a
  commissioned and connected router, and zero DMA overflows. After `LOG OFF`,
  another 12-second interval contained no periodic diagnostics.
- A four-second feature capture with diagnostics off delivered all 500 frames
  with integrity checks passing. This was an unlabelled protocol smoke test,
  not a recognition accuracy trial. Logging was left off for relocation.

### 2026-10-24: private waveform capture and host CNN experiment

- Added explicit USB waveform capture for the proposed event-triggered CNN.
  It streams the existing 16 kHz / 12-bit ADC samples without loss of sample
  precision; host preprocessing low-pass resamples them to 8 kHz. Raw capture
  skips DSP/FFT classification, suppresses Matter clicks and resumes with fresh
  calibration. It uses nonblocking USB writes and reports drops/ADC overflows.
- The pinned ESP32-C6 capture firmware compiled: 2,688,968 bytes of app flash
  and 144,444 bytes of static RAM. No CNN runtime or arena is included in those
  figures. No upload or physical waveform capture was performed on the
  workstation; the device will be recorded from a macOS M1 Pro laptop.
- All 82 default automated checks passed: 64 native C++ and 18 Python checks.
  New tests cover packet layout/CRC, fragmented input, exact ADC-to-PCM mapping,
  missing packets, aborted sessions, file-integrity checks, private output
  guards and proposed host double-clap timing. A simulated USB session exercised
  the host WAV writer; it is not a physical serial-bandwidth measurement.
- Optional TensorFlow 2.20.0 / Python 3.12 checks exercised the full host
  frontend, three-convolution/four-class network, training, INT8 conversion,
  recording-group split guards, full-record evaluation and C-array compilation
  using temporary synthetic recordings only. The conversion check produced a
  9,600-byte INT8 model with Conv2D, Mean, FullyConnected and Softmax operators.
  This is plumbing validation, not a trained clap-recognition result.
- Training dependency resolution was checked for Apple Silicon/Python 3.12;
  execution on that Mac remains untested. Real recordings, annotations and
  models remain private and no trained model is shipped or activated.
- On-device frontend parity, overlapping event-window queues, TFLite Micro
  integration, real tensor-arena/heap use, inference timing, and installation-room
  recognition/false-activation tests remain outstanding. The device's normal
  DSP detector remains the default.

### 2026-10-24: Apple Silicon capture preparation and physical waveform check

- Added a structured installation-room collection plan covering individual
  claps, object impacts, switch/click sounds and natural room background.
  Independent recording groups separate training, validation and final testing;
  longer no-clap recordings measure full-pipeline false double-clap activations.
- Installed isolated capture and Python 3.12 training environments on the Mac.
  All 82 default checks passed. The optional full synthetic training/INT8/export
  check also passed on Apple Silicon and produced a 9,600-byte model. Synthetic
  results do not measure real clap recognition.
- The Arduino-distributed ctags helper was Intel-only on this Mac. A local
  native build of Arduino's 5.8-arduino11 source, with its reserved attribute
  macros renamed to avoid a current SDK collision, allowed the pinned firmware
  build to complete. These local tools remain outside tracked source.
- The waveform firmware compiled to 2,689,080 bytes of app flash and 144,444
  bytes of static RAM. Upload and flash hash verification succeeded without a
  commissioning-data erase.
- A physical four-second unlabelled capture delivered all 64,000 ADC samples,
  valid packet integrity and a valid completion marker. ADC range was 700–1066,
  with zero near-rail samples and zero CRC failures. WAV integrity validation
  and host event proposals succeeded. This checks transport, not clap signal
  quality or a false-trigger rate; its two proposed events remain unreviewed.
- Raw audio, annotations and test logs remain under ignored `.local/`. The
  transport sample is excluded from the training manifest. Labelled physical
  training/evaluation sessions and embedded CNN inference remain outstanding.
- A subsequent USB status check showed normal DSP sampling, zero DMA overflows,
  retained commissioning, Thread attachment and router role. Logging was left
  off and the port closed. Runtime free 8-bit heap was 125,976 bytes, with a
  largest block of 110,580 bytes and minimum free heap of 125,924 bytes at that
  observation. These are available-heap readings, not a tensor-arena benchmark.

### 2026-10-24: binary labels for mixed real-room negatives

- Changed the host CNN output and report to clap/non-clap. Mixed recordings of
  objects, switches, doors, rattling and steady background can now share one
  verified negative label. Existing knock/click/other annotations map to
  non-clap; the waveform protocol and deployed DSP firmware are unchanged.
- Added default regressions for older annotation compatibility, verified mixed
  negatives and background sampling, and rejection of claps, unknown labels or
  unfinished labels in a negative-only recording. All 85 default checks passed.
- The optional synthetic end-to-end training, INT8 conversion, two-output
  inference, held-out replay and C-array compilation check passed on the Mac.
  Its 9,496-byte model verifies plumbing, not acoustic recognition.
- Private reviewed training data currently contains 49 clap windows and 214
  non-clap windows from three source recordings. The operator identified the
  mixed negative as placements, taps, switches, door slams, chain rattling and
  fan hum near its end. Loud sounds during the hum and the final impact were
  retained in complete negative windows. This is training data, not held-out
  evidence of a false-trigger rate.
- Waveform review exposed missed or displaced host trigger proposals on some
  claps; their training onsets were corrected manually. The trigger remains
  provisional and must be assessed with the CNN in full-recording evaluation.
  Independent validation/test sessions, a model trained on real audio, and
  embedded inference remain outstanding. All real audio and annotations stay
  private under ignored `.local/`.

### 2026-10-24: first real-audio binary CNN training and diagnostic replay

- Completed private train/validation/test groups with 100/38/45 reviewed clap
  windows and 1,079/216/438 non-clap windows respectively. Final captures passed
  integrity checks. The operator confirmed all final positive attempts were
  double claps; 22 pairs were clearly timed and one additional pair had an
  unresolved second onset. That annotation limit is retained in the reports.
- The 4,778-parameter CNN exported as a 9,496-byte fully INT8 model. Complete
  laptop training/evaluation/export runs took 12.8 and 11.83 seconds. No GPU
  migration was needed. The trained model and C-array header remain private.
- The original max-negative-window cutoff was 0.98828125: validation recall was
  16/38 claps, diagnostic recall 3/45 windows, and full replay detected zero
  of the 22 timed pairs. Its zero false actions did not establish useful
  recognition.
- Threshold selection now uses validation clap recall and full verified
  negative recordings through the trigger/classifier/pair rule. It selected
  0.8359375, retaining 35/38 validation claps, with four isolated false-clap
  classifications and zero validation false pairs. All 88 default checks and
  the synthetic training/INT8/export integration check passed.
- The rerun produced byte-identical model weights. Diagnostic replay accepted
  36/45 labelled clap windows and detected 9/22 timed pairs. All nine detections
  matched annotated pairs within 100ms per onset. There were zero false actions
  in 297.776 usable seconds of background and zero actions in the separate
  60-second single-clap validation recording. These short observations do not
  establish a low long-term false-trigger rate.
- Supplying reviewed onsets directly to the classifier yielded 13/22 pairs;
  the current trigger reduced this to nine. Both classification and onset
  proposals therefore need improvement. The model is not ready for deployment.
- Session C was inspected while diagnosing the initial threshold failure.
  Subsequent reuse is explicitly marked diagnostic; new independent held-out
  sessions are needed after further changes. The existing board still runs DSP
  firmware. Embedded CNN frontend parity, queues, arena/heap usage and timing
  remain unimplemented or unmeasured.

### 2026-10-24: existing-sample CNN improvement

- Kept the reviewed annotations and recording-group split. Training still uses
  group A (100 claps / 1,079 non-clap windows), with no evaluation audio mixed
  into augmentation. Gain, timing, noise, spectral balance, speed and short
  reflection variants produced 2,170 clap / 7,637 non-clap training examples.
- Changed the host proposals to 700 Hz high-band attacks with 32 ms cooldown,
  so a rejected precursor cannot suppress the next clap for 160 ms. Stronger
  1 kHz proposals supply extra training examples. Accepted windows are grouped
  within 120 ms of the first candidate and timestamped by attack strength,
  fixing duplicate recognition that cancelled a valid pair.
- Normalizing waveform RMS before the log floor and retaining spatial features
  with Flatten improved quiet-clap recognition. The 5,114-parameter network
  exported as a fully INT8 9,944-byte model using only Conv2D, Reshape,
  FullyConnected and Softmax. The batch dimension is fixed to one.
- Class-balanced validation loss and full-stream threshold selection retained
  all 38 validation claps at cutoff 0.76171875. Two isolated background
  candidates were accepted, with no false validation actions and no actions
  during the separate single-clap validation recording.
- Complete diagnostic replay matched 20/22 timed pairs, up from 9/22, within
  100 ms for both onsets. No unmatched pairs were detected. Reviewed-window
  recall improved from 36/45 to 42/45. The remaining missed pairs begin at
  23.010 s and 44.868 s. The additional pair with an unresolved second onset
  remains excluded; no annotation was changed to match model predictions.
- No false actions occurred in the 297.776 usable seconds of diagnostic
  background, or in any other existing verified negative recording. Combined
  exposure is 1,131.104 seconds, including 715.552 seconds used by training;
  this combined count is diagnostic, not independent validation of a low rate.
- The reproducible complete laptop run took about 40 seconds. The model, float
  checkpoint, frozen protocol, scores and matched-timestamp report remain
  private under `.local/`. All 95 default checks, numeric frontend regressions
  and the synthetic train/INT8/export/C-header integration check passed.
- Group C is a development diagnostic because its failures informed these
  changes. No CNN model was flashed or enabled. MCU frontend parity, memory,
  queue load and inference timing still need measurement with Thread active.

### 2026-10-24: private CNN deployed and live lamp test

- Added opt-in CNN builds using private generated model/frontend assets, with
  the existing ESP32 3.3.12 TensorFlow Lite Micro and ESP-NN libraries. Normal
  builds retain DSP. No audio, annotation dataset, model weights or generated
  inference fixtures were added to Git. The previous DSP image is retained
  privately for rollback; uploading preserved Matter/Thread commissioning.
- Ported centered 16-to-8 kHz resampling, the proposal gate, RMS-normalized
  log-mel input, INT8 quantization, timestamp association and the exactly-two
  pair rule. Candidate timestamps and classifier watermarks preserve third-clap
  cancellation despite delayed overlapping windows. ADC acquisition continues
  by DMA; inference runs only after the ADC is drained and Thread is connected.
- Initial floating-point frontend builds were too slow and dropped queued
  windows. The final frontend uses Q30 FIR/Hann/twiddle coefficients, Q24 FFT
  arithmetic, integer accumulation and precomputed quantized log boundaries.
  A 16-candidate queue and 1.024-second ring absorb burst load. Queue/ring faults
  discard the gesture and recalibrate, with persistent drop counters.
- Native replay compared 7,698 candidates across every existing negative
  recording and the validation/diagnostic positive recordings. Input codes
  differed from TensorFlow by at most one step, with no classifier decision
  changes. The embedded gesture implementation preserved all pair outcomes:
  20/22 timed diagnostic matches, no unmatched diagnostic pairs, and zero false
  actions in the existing negative recordings. This remains diagnostic data.
- The device passed synthetic frontend/quantization and positive/negative
  inference fixtures at boot. Tensor arena use is 3,052 bytes, with 8,192 bytes
  reserved. The final app uses 2,782,964 bytes (88% of the 3 MiB partition) and
  197,004 bytes of static RAM. The retained DSP build also compiled successfully.
- Live frontend work typically took about 19.6 ms and inference 5.6–6 ms.
  Observed wall-time peaks reached 29.530 ms frontend, 10.977 ms inference and
  37.432 ms combined service, including task preemption. Heap was 99,596 bytes
  free with an 84,764-byte minimum and a 53,236-byte largest block. These values
  apply to this model/core/build rather than a general resource guarantee.
- The operator made five double-clap attempts. All five generated separate
  Matter button clicks; the operator confirmed the lamps went on and off.
  The board remained commissioned, connected and a Thread router. Final status
  showed 738 classified proposals, zero queue drops and zero DMA overflows.
  Verbose diagnostics were switched off after testing.
- All 99 default checks passed, including four new native CNN gesture checks.
  Private build manifests, firmware, parity results and device logs remain
  under `.local/` and `.build/`. Longer fresh positive/negative trials are still
  needed before claiming a reliable recognition or false-trigger rate.

### 2026-10-24: DS-CNN experiments retained offline

- Saved and verified a dedicated rollback snapshot of the working model,
  float checkpoint, cutoff, frontend, sources and exact firmware binaries.
  The existing INT8 model hash remains unchanged. The default flash files were
  restored to that original image after compiling experimental firmware.
- Tested six DS-CNN variants using the same recordings, labels, augmentation,
  frontend, seed and threshold policy. The two-block Flat/GAP models matched
  18/22 timed diagnostic pairs and produced 1/4 false actions on all existing
  negative recordings respectively. Wider two-block models matched 19/22 and
  18/22, each with one false action. Three-block, batch-normalized Flat/GAP
  variants matched 18/22 each with zero observed false actions. The original
  standard CNN retained the better 20/22, zero-false-action tradeoff.
- Kept all weights, actual labels, audio and detailed reports local. Existing
  group C remains diagnostic; these comparisons do not establish unseen-room
  performance. Parameter/export-size results are in the CNN experiment guide.
- Added optional depthwise/mean operator support and a private shadow-only
  benchmark bank. It compiled for the C6, and synthetic tests verified static
  INT8 conversion and batch-normalization folding. It was **not flashed**.
  A normal build using the archived original model/frontend assets also
  compiled without regenerating the weights.
  No C6 timing or arena measurements are claimed for the new architectures.
- The operator disconnected the C6 and requested more negative examples before
  deploying new networks. The device remains on the prior working firmware.
  A targeted training-negative session and a separate reserved no-clap session
  are planned next; the recording plan includes local capture commands.
- All 101 default checks passed, including saved-image upload selection and
  hash-mismatch rejection with hardware calls mocked. The optional seven-model
  synthetic export test also passed. Uploading a saved firmware directory uses
  `flash --input-dir`, while experimental builds can use a separate output
  directory to avoid replacing the default flash files.

### 2026-10-25: faster first-pair behavior trial

- Retained the original working CNN weights and cutoff, with the original exact
  firmware image still preserved for rollback. Installed a timing-only build
  that emits after the second classified association cluster, without waiting
  600 ms for a third clap to be absent. Longer bursts emit once and rearm after
  600 ms quiet. The existing 160–599 ms onset range includes 200–300 ms pairs.
- Kept legacy exact-two behavior selectable in native/host evaluation and
  firmware builds. New training defaults to the immediate policy, so cutoff
  selection and false-action evaluation match the intended device behavior.
- Full native replay under the immediate policy retained 20/22 timed diagnostic
  pair matches, with zero changed classifier decisions and zero actions in all
  existing negative recordings. These remain previously inspected diagnostics.
- The timing-only firmware built successfully and was flashed with pairing
  retained. Device status confirmed the original model hash, immediate policy,
  shadow mode off, a Thread router connection, and zero queue/DMA drops during
  observation. The operator completed the faster-pair trial and reported “much
  better.”
- Added experiment-local manifest snapshots and evaluation of frozen weights
  and cutoffs on separate test-only manifests. The evaluator rejects a fresh
  held-out label for recordings already present in the model experiment. This
  supports new training impacts/background alongside existing data while
  reserving separate impacts/background for the final evaluation.
- All 108 default checks passed, including immediate 200/250/300 ms timing,
  one action per burst, legacy behavior and manifest relocation. The synthetic
  train/export/evaluate/C-header integration also passed. Data and models stay
  under ignored local storage; new negative recording collection is prepared.

### 2026-10-25: additional negatives and frozen held-out comparison

- Collected nine minutes of new training negatives: three minutes of varied
  impacts, one minute of wardrobe doors and five minutes of room background.
  Combined these with the original training recordings, retaining the original
  validation split and treating the previously inspected positive test as
  diagnostic. Kept loud/clipped impacts as real negative examples.
- Compared eight new runs: a standard CNN retrain, three DS-CNN variants,
  two standard-CNN runs with training-only hard-negative refinement, and two
  gentle fine-tunes starting from the working float checkpoint. No candidate
  improved on the working model's 20/22 timed diagnostic pair matches with
  zero observed negative actions. The strongest clean retrains matched 19/22.
- Froze the unchanged working model and one fine-tuned standard-CNN challenger,
  including their cutoffs and hashes, before evaluating two separate reserved
  recordings: three minutes of thuds/object/wardrobe activity and five minutes
  of ordinary background with occasional thuds. Neither recording was used
  for training, early stopping, cutoff choice or candidate selection.
- Both models produced zero double-clap actions over 475.552 usable negative
  seconds. The working model accepted 15 isolated noise transients, versus 12
  for the challenger; none formed an activating pair. Eight minutes of this
  activity is limited evidence, not a long-term false-trigger-rate guarantee.
- Retained the original model and installed faster-response firmware. No new
  model was flashed. Verified that the working model still matches its saved
  rollback copy. Audio, labels, checkpoints, model weights, training adapters
  and detailed comparison/evaluation reports remain ignored local artifacts.

### 2026-10-25: podcast failure captured and conservative retrain prepared

- The operator reported repeated unwanted lamp actions during a podcast.
  Captured three minutes for training and another three-minute excerpt for a
  reserved comparison. Both transfers were complete with zero CRC errors.
  The operator clarified that quiet first-two-second starts were inconsistent
  across the collected recordings. Retained the actual starts for gate
  calibration and the existing two-second exclusion; negative content labels
  remain valid. Do not assume protocol compliance for individual files.
- Combined the original training recordings with all new training negatives,
  including the formerly reserved impact/background recordings. The original
  validation group remains separate; the previously inspected clap test remains
  diagnostic. The former reserved negatives are now training data and supply
  no independent evidence for these new models.
- Kept the standard 5,114-parameter CNN, RMS/log-mel frontend and immediate-pair
  behavior. Compared a new scratch retrain, two fine-tunes from the working
  checkpoint and two training-only hard-negative refinements. All actual
  annotations, manifests, training adapters and model artifacts remain local.
- The first podcast clip did not reproduce an activating pair under the original
  model, although it accepted 20 isolated sound clusters. Native C++ replay
  matched those decisions. The operator then explicitly prioritized avoiding
  false actions over dependable clap recall. Before predicting on the second
  clip, declared a comparison of the original model, the refined fine-tune and
  the more conservative scratch retrain, with all weights and cutoffs frozen.
- On the separate podcast excerpt (177.776 usable seconds), the original model
  produced one false double-clap action and accepted 20 noise clusters.
  The fine-tuned candidate produced zero actions and accepted nine clusters.
  The conservative candidate produced zero actions and accepted one cluster.
  Their previously inspected diagnostic clap matches were 20/22, 19/22 and
  17/22 respectively. This short comparison supports a conservative trial;
  it does not establish elimination of false actions in ordinary use.
- Prepared the conservative model for a device trial. The original working
  weights, installed timing-only firmware and rollback image are preserved.
  Native C++ frontend/gesture replay passed on the development recordings and
  fresh excerpt, retaining both models' action outcomes. An isolated C6 firmware
  build passed with the immediate policy and shadow mode off. No new model has
  been flashed at this point.

### 2026-10-25: conservative model installed and physical pair trial

- On operator instruction, uploaded the verified conservative standard-CNN
  image with the immediate-pair policy and shadow mode off. Upload hash
  verification and frontend/inference startup self-tests passed. Tensor arena
  use remains 3,052 bytes with 8,192 reserved; the model remains 9,944 bytes.
- Matter commissioning was preserved and Thread connected as a router. Status
  confirmed the expected model, cutoff and policy, with no queue drops or DMA
  overflows. Kept both the original exact-two firmware and original-model
  faster-response firmware privately for rollback.
- The live log recorded five distinct Matter clicks during the five-attempt
  double-clap trial. The operator confirmed that all five toggled the lamps
  perfectly. The podcast was paused during these attempts; this interval is
  positive-test evidence only, not a podcast false-trigger trial.
- After the operator explicitly confirmed playback, observed another 180.7
  seconds of podcast audio with no deliberate claps. Four candidate windows
  were accepted, but none formed an activating pair: zero additional Matter
  clicks. Excluded the earlier paused interval from this negative trial.
- End status showed 2,232 classified candidates, peak queue depth 12 of 16,
  zero queue drops, zero DMA overflows and no inference failure. Heap was
  99,684 bytes free with an 83,256-byte minimum. Observed frontend/inference
  wall-time peaks were 36.642/16.278 ms, including preemption; typical work
  remained about 20/6 ms. Diagnostics were disabled and the USB monitor closed.
- Left the conservative trial installed. These five positive attempts and
  three minutes of confirmed playback are initial observations, not evidence
  that false triggers have been eliminated in longer ordinary use.
