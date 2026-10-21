# Thread ClapClap

A four-wire, USB-powered **double-clap button for Home Assistant**, using a
Seeed XIAO ESP32-C6 and Waveshare Sound Sensor. An analog audio detector uses
adaptive thresholds and FFT features to recognise short broadband bursts. Two matching bursts send one Matter Generic Switch
click over Thread. The firmware also enables Thread
router eligibility to let the device help relay mesh traffic.

**Status:** the analog FFT firmware compiles with Arduino ESP32 **3.3.12**,
and all **45 host tests** pass. The earlier digital version was flashed,
commissioned into Home Assistant, and automatically became a Thread router.
The analog version has also delivered a real double-clap event through
Home Assistant to the lamp group. Recognition is still being tuned; real-room
precision and recall have not been measured. This is an
MIT-licensed community prototype, not a certified Matter product.

## 1. Connect the hardware

**Unplug USB before wiring.** Use four female-to-female jumper wires. The
XIAO must have its headers fitted, and the sensor must have accessible pins.

![Four-wire wiring diagram](docs/wiring.png)

| Waveshare sound sensor | XIAO ESP32-C6 | Example wire colour |
| --- | --- | --- |
| VCC | **3V3** | Red |
| GND | **GND** | Black |
| DOUT | **D1 / GPIO1** | Yellow |
| **AOUT** | **D0 / GPIO0** | **Blue — new analog connection** |

The diagram shows the XIAO's **labelled underside facing you, with USB-C at
the top**: D0 is the top-right pin and D1 is the second down on the right.
GND is the second down on the left; 3V3 is the third down on the left.
The component-side view mirrors those positions. Follow the **printed sensor pin labels**:
its connector in the illustration is schematic, not a physical pin-order guide.
Wire colours are your choice.

Power the sensor from **3V3**, keeping DOUT at ESP32-compatible logic voltage.
Do not use the 5V header pin for this connection. The sensor's DOUT is
**active-low**: loud sound pulls it LOW.

For an upgrade from the original three-wire version, **keep DOUT → D1** and
add **AOUT → D0**. The supplied Waveshare lead used for AOUT on this build is
blue; follow its pin label rather than assuming cable colours are standard.

If you used the old discussion's DOUT → D0 wiring, move that wire to **D1**
first: D0 is now reserved for analog audio. Do not connect AOUT and DOUT to
the same GPIO. `Config.h` selects analog audio by default. The original
DOUT-only mode remains available by setting `useAnalogAudio = false` and
rebuilding; that mode does not run the FFT filters.

Once checked, connect a **USB-C data cable** to the computer. After flashing,
a standard USB supply can power it continuously.

## 2. Build and flash

Install [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/)
and Python 3.10 or newer. Commands below run from the repository root; use
`python` in place of `python3` on Windows if needed.

```sh
python3 tools/project.py setup
python3 tools/project.py build
python3 tools/project.py ports
python3 tools/project.py flash --port /dev/ttyACM0
python3 tools/project.py monitor --port /dev/ttyACM0
```

Replace `/dev/ttyACM0` with the detected port (for example **COM7** on Windows,
or `/dev/cu.usbmodem…` on macOS). `setup` downloads and installs the pinned
ESP32 core and its toolchains. `flash` uploads the last local build; rebuild
after changing the source. Close other serial monitors before uploading.
The console runs at **115200 baud**. Press RESET after opening it if you missed
the startup pairing code.

The script selects `XIAO_ESP32C6`, USB CDC enabled, and **Huge APP (3MB No OTA)**.
The analog build uses approximately 2.68 MB of flash (85% of that app
partition). There is no OTA update implementation; updates use USB.

For Arduino IDE, add Espressif's board-manager URL
`https://espressif.github.io/arduino-esp32/package_esp32_index.json`, install
**esp32 by Espressif Systems 3.3.12**, and open
[`ThreadClapClap.ino`](firmware/ThreadClapClap/ThreadClapClap.ino).
Select **XIAO_ESP32C6**, **Huge APP (3MB No OTA/1MB SPIFFS)**, and **USB CDC On
Boot: Enabled**. Keep Zigbee mode disabled. No separate libraries are needed.

### Windows / WSL

Windows USB devices are not automatically visible in WSL. You can use Arduino
CLI/IDE directly on Windows, or attach this board to WSL with
[usbipd-win](https://learn.microsoft.com/windows/wsl/connect-usb):

```powershell
# Windows PowerShell: list devices and identify the ESP32 USB/JTAG serial device.
usbipd list
# Run once in an Administrator PowerShell (replace the example BUSID).
usbipd bind --busid 4-4
# Run in a regular PowerShell while WSL is running.
usbipd attach --wsl --busid 4-4
```

Run `python3 tools/project.py ports` in WSL afterwards. BUSIDs can change after
unplugging; attach only the XIAO. If the upload port disappears, hold BOOT,
press and release RESET, then release BOOT to enter the USB bootloader; list
ports again and retry the upload. Press RESET afterwards to run the firmware.

## 3. Pair with Home Assistant

You need an existing, operational **Thread Border Router**, Home Assistant's
Matter integration/Matter Server, local IPv6 connectivity, and the Home
Assistant Companion app on a phone with Bluetooth. The phone must know your
Thread network credentials. The clapper joins that network; the firmware never
creates its own Thread dataset.

1. Start the board and open its serial console. Find **Manual pairing code**
   or open the printed QR code URL on a second screen.
2. In the Companion app, add a **new Matter device** and scan that code or
   enter the manual code. Approve the development/uncertified-device prompt
   if your controller displays one. Commissioning uses BLE; normal events
   use Thread. See the [official Matter setup guide](https://www.home-assistant.io/integrations/matter/)
   for platform-specific pairing requirements, including Android development-device restrictions.
3. Once paired, the console should report `commissioned=yes connected=yes`.
   Home Assistant exposes a **button event entity** for the Generic Switch.
4. Tune the microphone and try two claps. The console prints
   `Double clap -> Matter button click` when it sends the event.

This development build uses the SDK's example commissioning credentials and
test attestation certificates. It is intended for a personal prototype, not
commercial distribution. Do not post your Thread dataset or commissioned
device storage in issues or commits.

If pairing fails, check the [Thread setup and credential guide](https://www.home-assistant.io/integrations/thread/).
To reset pairing, **hold BOOT for five seconds after the firmware has booted**.
That clears the Matter fabrics and Thread credentials and restarts the board.
Holding BOOT during reset instead enters the USB bootloader.

## 4. Add a double-clap automation

The firmware deliberately maps **two physical claps → one Matter click**.
Single claps do nothing. Home Assistant sees `initial_press`, followed by
`short_release`; it does not see a Matter double-press gesture.

Use the device's **button pressed** trigger in the automation editor, or import
[`home-assistant/double-clap-action.yaml`](home-assistant/double-clap-action.yaml)
as a blueprint using this URL:

[Import the blueprint into Home Assistant](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fcrazynucfan%2Fthread-clapclap%2Fblob%2Fmain%2Fhome-assistant%2Fdouble-clap-action.yaml)

```text
https://github.com/crazynucfan/thread-clapclap/blob/main/home-assistant/double-clap-action.yaml
```

Choose the clapper's Matter **event entity** and the action to run, such as
`light.toggle`. The blueprint filters `initial_press` so the accompanying
release cannot toggle your light a second time. It also ignores entity
restoration and availability changes. Avoid triggering indiscriminately on
every change to the event entity.

### Lamp-toggle example

The dedicated [lamp-toggle blueprint](home-assistant/double-clap-lamps.yaml)
provides pickers for the Matter event and light/switch entities:

[Import the lamp-toggle blueprint](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fcrazynucfan%2Fthread-clapclap%2Fblob%2Fmain%2Fhome-assistant%2Fdouble-clap-lamps.yaml)

The [generic lamp example](home-assistant/examples/toggle-lamps.yaml)
connects `event.clap_button` to `switch.lamp_group`. These are
placeholder IDs: replace them for your own installation. A private instance
of this automation has been installed and its action was tested: the lamp group changed
**off → on → off**. A later physical double clap also reached this automation and switched the
lamp group on. That demonstrates one working path, not reliable recognition
across all attempts. Using one existing lamp group keeps its members'
on/off action coordinated.

## 5. Tune analog sound filtering

Keep the room quiet for **two seconds after startup or reconnection**. The ADC
samples AOUT on D0 at 16 kHz using DMA; this does not depend on the timing of
`loop()` or repeated `analogRead()` calls. A 256-sample Hann window with 50%
overlap gives a new FFT every 8 ms, with 62.5 Hz frequency bins. The
streaming filter and FFT use fixed-point integer arithmetic because the C6
has no floating-point unit; final diagnostic features are floating point. Samples stay
in a small RAM buffer; audio is not recorded to storage or transmitted.

The detector combines several tests, rather than treating an FFT as a clap
classifier:

1. A high-pass filter removes DC bias and attenuates low-frequency rumble.
2. An adaptive room-noise estimate sets the amplitude threshold. A sound must
   exceed both an absolute RMS floor and a multiple of the noise RMS.
3. A sudden onset and a short duration are required. Sustained or slowly
   rising sound is rejected, then the detector rearms after the background settles.
   The release threshold follows 25% of the burst peak so quiet reverberation
   does not extend an otherwise short clap indefinitely.
4. The **loudest frame** must have enough high-frequency content and a spread
   of FFT energy. Spectral flatness and concentration reject narrowband tones;
   crest factor rejects a smooth sine wave and isolated electrical spikes.
   A strong burst with at least 20 significant bins can support a pair despite
   low flatness, provided its RMS is at least 150 counts and 12 times ambient
   noise and its concentration is at most 0.40. Timing, high-band content,
   crest, and clipping checks still apply.
5. Clipped bursts are rejected because saturation corrupts the spectral tests.
6. Two accepted bursts must arrive roughly 160-600 ms apart and have reasonably
   similar strength and spectral balance. At least one must pass the strict
   flatness check; two low-flatness bursts cannot trigger on their own. Either
   burst can supply the strict evidence. A third comparable burst cancels the pair.

The event still waits for the 600 ms window after the second onset to close,
and Home Assistant still sees **one button click**, so the same automation
blueprint works. Sustained broadband noise and clipping clear pending claps.
Rejected tones preserve pending claps. A much weaker candidate is ignored; a
stronger or spectrally different candidate starts a new sequence, allowing a
genuine pair to follow unrelated sound. A fresh strong onset can also interrupt
a weak background candidate. Echo tails inside the 160 ms lockout do not
erase a genuine first clap. The supplied automations queue up to ten runs so
closely delivered clicks can each execute their action. Data loss resets the
stream and calibration; there is no automatic fallback to the less selective
DOUT-only detector. Offline gestures are discarded.

These are tunable **heuristics**, not a trained clap recogniser. A short
broadband door slam, hand tap, or recorded clap can resemble a genuine clap.
Synthetic tests establish expected algorithm behaviour; they do not measure
real-world precision or recall. The LM386 module also has no audio anti-alias
filter specified by this project, so the frequency features are approximate.

### Read the diagnostics before changing thresholds

Every two seconds the console prints the input RMS, adaptive noise estimate,
raw ADC bias/range, clipping fraction, counters, DMA overflows, and the maximum
processing time for a 128-sample batch. Each completed candidate prints its
classification, for example `accepted`, `rejected_spectrum`,
`rejected_slow_attack`, `rejected_clipping`, `rejected_duration`, or
`restarted_stronger_or_different`, `ignored_weaker`, or `rejected_pair_spectrum`.
The last means two candidate bursts lacked strict spectral evidence. Thread status also
reports neighbour signal/link quality and MAC acknowledgement/retry counters;
a connected router status alone does not prove timely Matter delivery.

The `high`, `flat`, and `tone` fields are ratios, not calibrated sound levels.
`bins` counts in-band FFT bins holding at least 1/128 of total band power;
`crest` is peak divided by RMS. `strict=yes` means the loudest frame passed
all strict spectral/crest checks; `accepted` marks an individual candidate,
not a complete gesture. Both `strict=no` candidates can be accepted without
producing a click. These fields help explain spectral rejections.
`raw` and `range` are ADC counts, not millivolts. `max_batch_us` includes
classification/logging time and task preemption; it is a diagnostic, not a
hard real-time deadline guarantee. Repeated DMA overflows mean the stream is
not keeping up and need fixing before acoustic results can be trusted.

Start with two claps about **300 ms apart**, then pause for a second. The gain adjuster is the **small white screw marked A beside the round
microphone**. Use a precision screwdriver with a small flat blade that fits
the slot (roughly 1-2 mm), and turn it gently in small steps. Adjust
**A gain** on the Waveshare board so claps are clear without
`rejected_clipping` messages. The **D threshold potentiometer does not set the
analog classifier's sensitivity**; it affects DOUT, which analog mode does
not use to approve gestures. If the ADC stays near a rail or the input never
changes, check the blue AOUT → D0 connection.

The defaults live in `AudioClapDetector::Config` in
[`AudioClapDetector.h`](firmware/ThreadClapClap/AudioClapDetector.h). Override
fields through `audioDetector` in `Config.h`, then rebuild and flash:

| Parameter | Default | Effect |
| --- | --- | --- |
| `calibrationMs` | 2000 | Quiet startup learning period |
| `minRms` | 18 counts | Minimum absolute sound amplitude |
| `noiseMultiplier` | 4 | Minimum amplitude relative to ambient RMS |
| `attackRatio` | 2.2 | Required abrupt rise |
| `minHighRatio` | 0.15 | Minimum 1.5-7.5 kHz share of 250 Hz-7.5 kHz energy |
| `maxConcentration` | 0.55 | Maximum energy share in three neighbouring bins |
| `minFlatness` | 0.08 | Strict spectral flatness; at least one clap per pair must pass |
| `minSpreadBins` | 20 | Significant bins required to support a low-flatness burst |
| `minSpreadRms` | 150 counts | Absolute RMS floor for low-flatness support |
| `spreadNoiseMultiplier` | 12 | Ambient-noise margin for low-flatness support |
| `maxSpreadConcentration` | 0.40 | Concentration limit for low-flatness support |
| `minCrest` / `maxCrest` | 1.7 / 12 | Peak-to-RMS bounds |
| `maxClippedFraction` | 0.05 | Maximum near-rail sample fraction |
| `peakReleaseRatio` | 0.25 | Release after RMS falls below 25% of the peak |
| `maxBurstMs` / `quietMs` | 120 / 24 ms | Maximum burst and release quiet time |
| `minClapGapMs` / `sequenceGapMs` | 160 / 600 ms | Double-clap timing |
| `maxPairRmsRatio` | 4 | Maximum strength ratio between the two bursts |
| `maxPairHighDifference` | 0.35 | Maximum high-frequency balance difference |

For the original digital mode, `config::detector` still sets the 40 ms quiet
period, 120 ms maximum burst, 160 ms echo lockout, and 600 ms sequence timeout.
Its DOUT edge queue and previous behaviour remain available for comparison.

## Thread router behaviour

The pinned ESP32-C6 prebuilt SDK has **Full Thread Device (FTD)** support.
The firmware checks this at compile time, keeps the receiver on, and enables
router eligibility using the OpenThread API. It logs the actual role and
eligibility every ten seconds, for example:

```text
Thread: role=child router_eligible=yes config_error=0
Thread: role=router router_eligible=yes config_error=0
```

Thread decides whether and when to promote an eligible device. A child role
can be normal; the firmware does not force promotion. Keep it on USB power if
you want it to relay traffic. It is a **Thread mesh device**, and you still need
your existing Border Router to connect Thread to the Home Assistant LAN.

## Development and validation

```sh
# Requires g++ or CXX=clang++; no board or Arduino installation needed.
python3 tools/project.py test
```

The **45 host tests** include the original 14 digital tests plus 31 analog
analysis/classification tests. Analog fixtures cover DC removal, a sinusoid's
FFT/RMS, broadband noise, valid pairs across window alignments, ignored singles
and triples, tonal and low-frequency pairs, sustained noise and recovery,
slow attacks, clipping, electrical spikes, mismatched pairs, echoes, resets,
and `millis()` rollover. They also cover reverberant clap tails and a feature
fixture based on the first physical clap test, exclusion of release-quiet
time from burst duration, and feature timelines with weak precursors, tonal
interruptions, strong onsets inside background sound, and a third burst.
A later labelled pair supplies a regression for uneven spectral flatness;
additional cases require a strict anchor, reject weak/narrow/low-SNR support,
and prevent reuse of strict evidence after a sequence restart or timeout. These tests are not a recorded
real-room accuracy benchmark.

Analog mode uses ADC DMA with bounded processing batches. The main loop
performs the FFT and detection and submits Matter clicks outside interrupt
context. Thread and Matter retain their own tasks. Digital mode instead
captures both GPIO edges into its bounded queue.

The optional [GitHub Actions workflow template](ci/github-actions.yml) runs the
host tests, builds the pinned firmware, and uploads the build files as an
artifact. To enable it, copy it to `.github/workflows/ci.yml` and commit using
a GitHub login/token with permission to write workflows. It is supplied as a
template because the publishing token did not have that permission.

A successful build does **not** establish that commissioning or acoustic
performance works on hardware. Before describing this example as
hardware-validated, record results from the
[hardware validation checklist](docs/hardware-validation.md).

For an isolated toolchain, the project helper accepts `--cli /path/to/arduino-cli`,
`--cli-config /path/to/arduino-cli.yaml`, and `--build-dir /path/to/build` before
the subcommand. Build products are ignored by Git.

## Sources

- [Seeed XIAO ESP32-C6 pinout](https://wiki.seeedstudio.com/xiao_esp32c6_getting_started/)
- [Waveshare sensor analog/digital outputs and adjustment controls](https://www.waveshare.com/wiki/Sound_Sensor)
- [Arduino ESP32 3.3.12 source](https://github.com/espressif/arduino-esp32/tree/3.3.12)
- [Matter Generic Switch API](https://docs.espressif.com/projects/arduino-esp32/en/latest/matter/ep_generic_switch.html)
- [Home Assistant Matter event handling](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/event.py)
- [ESP32-C6 continuous ADC driver](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c6/api-reference/peripherals/adc_continuous.html)
- [OpenThread router API](https://openthread.io/reference/group/api-thread-router)

Released under the [MIT licence](LICENSE).
