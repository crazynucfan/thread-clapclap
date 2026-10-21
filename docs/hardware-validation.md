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
