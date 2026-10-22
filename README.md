# Thread ClapClap

A four-wire, USB-powered **double-clap button for Home Assistant**, using a
Seeed XIAO ESP32-C6 and Waveshare Sound Sensor. An analog audio detector uses
adaptive thresholds, FFT onset flux, and burst-shape scoring to recognise short impulses. Two matching bursts send one Matter Generic Switch
click over Thread. The firmware also enables Thread
router eligibility to let the device help relay mesh traffic.

**Status:** the current burst-scoring firmware builds with Arduino ESP32
**3.3.12**. All **73 automated checks** pass: 62 native C++ checks (including
45 retained legacy checks) and eleven capture/replay checks. Earlier firmware
was commissioned into Home Assistant, became a Thread router, and delivered
physical double-clap events, but subsequent trials frequently missed. A labelled physical pair passed the new classifier and was missed by the old
one on the same captured frames. A subsequent live pair also delivered a
button event to Home Assistant. A separate speech/tapping stress trial
produced one false trigger with each classifier. Installation-room recognition
and false-trigger rates still need validation. This is
an MIT-licensed community prototype, not a certified Matter product.

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

Verbose diagnostics are **off after each reboot**. In the serial monitor,
send `LOG ON` followed by a newline to see detector decisions, audio counters,
and Thread status. Send `LOG OFF` to stop them. This skips diagnostic formatting
as well as transmission; detection, counters, and Matter events continue.
Startup information and errors may still print. Feature capture works with
diagnostics off. No computer is needed during normal USB-powered operation.

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

The default detector now scores the **attack and envelope of a burst**:

1. The adaptive ambient RMS estimate gates candidate sounds. A sudden rise and
   a peak within 40 ms are required.
2. Positive **spectral flux** measures how much frequency-bin magnitude rises
   between adjacent FFT frames. Significant bins that more than double add
   evidence for a broad onset. The fixed-point path uses L1 magnitude to avoid
   a square root per bin on the C6.
3. During the first 32 ms, the detector gathers spectral evidence across frames:
   occupied bins, rising bins, flatness, concentration, and high-band energy.
   It uses the best eligible attack frame, weighted by its RMS relative to
   the final peak. This limits evidence contributed by weak precursors.
4. It measures the share of above-noise frame energy in that attack window.
   This describes front-loaded impulses and their decay.
5. A weighted score combines SNR (20%), attack (20%), flux (20%), spectral
   evidence (25%), and front energy (15%). Default acceptance is 0.70. The
   weights are engineering defaults awaiting real-room calibration, not a
   trained or probabilistically calibrated confidence model.
6. Clipping, extreme crest factor, slow peaks, low spectral evidence, and
   excessive duration still reject candidates. A single low-flatness or
   low-high-band frame no longer vetoes an otherwise convincing impulse.
7. Two accepted candidates must arrive 160–599 ms apart with compatible
   strength and high-band balance. Comparable third candidates cancel the
   gesture; much weaker interruptions are ignored. A much stronger/different
   candidate restarts the sequence. Echoes inside the lockout are ignored.

One Matter click is emitted after the 600 ms window following the second
onset closes. Sustained broadband noise or clipping cancels pending claps;
rejected tonal backgrounds preserve them. Stream loss and connection changes
clear the gesture and recalibrate. Offline gestures are discarded.

These remain **heuristics**. Short taps, slams, or recorded claps can resemble
hand claps. The module has no audio anti-alias filter specified by this
project. Synthetic checks do not establish precision or recall.

### Capture and replay before tuning

The [calibration guide](docs/audio-calibration.md) explains how to capture
**every FFT feature frame**, label positive and negative trials, and replay
both the new and previous classifier without reflashing. Captures contain
summary features, not waveform samples, and stay in the ignored `.local/`
directory. USB is needed for calibration only; normal detection runs on the
board with USB power and Matter over Thread.

Capture mode suppresses Matter clicks. CRC, sequence, sample-clock, and
completion checks reject interrupted or lossy captures. The build helper
embeds a detector-source fingerprint so replay can check the live firmware's
click count when the source/configuration matches.

### Read the diagnostics

Normal serial output includes `score`, `spectral`, `flux`, `front`, `rise`,
`attack`, and `pending`. The score is weighted evidence, not a probability.
The displayed spectrum belongs to the loudest frame; the classifier also
uses earlier attack frames. Each rejection identifies confidence, spectrum,
crest, attack, clipping, or duration. Status lines include DMA overflows and
maximum processing time per 128-sample batch. Repeated overflows must be
resolved before recognition results can be trusted.

The defaults live in `ImpulseClapDetector::Config`; override them through
`audioDetector` in [Config.h](firmware/ThreadClapClap/Config.h), rebuild, and
replay held-out captures before another physical trial.

| Parameter | Default | Effect |
| --- | --- | --- |
| `calibrationMs` | 2000 ms | Quiet startup learning |
| `minRms` / `noiseMultiplier` | 18 / 4 | Absolute and ambient-relative gates |
| `minAttack` / `maxRiseMs` | 2.2 / 40 ms | Sudden onset and prompt peak |
| `attackWindowMs` | 32 ms | Early spectral evidence and energy window |
| `minScore` / `minSpectralScore` | 0.70 / 0.25 | Overall and spectral evidence floors |
| `minCrest` / `maxCrest` | 1.7 / 12 | Peak-to-RMS bounds |
| `maxClippedFraction` | 0.05 | Near-rail sample limit |
| `peakReleaseRatio` | 0.25 | Peak-relative release level |
| `maxBurstMs` / `quietMs` | 120 / 24 ms | Burst limit and release confirmation |
| `minClapGapMs` / `sequenceGapMs` | 160 / 600 ms | Echo lockout and pairing window |
| `maxPairRmsRatio` / `maxPairHighDifference` | 4 / 0.35 | Pair consistency |

Start with claps roughly 300 ms apart. The sensor's **A gain** screw beside
its microphone takes a small precision flat screwdriver (roughly 1–2 mm).
Use small adjustments only if the measurements show inadequate signal or
clipping. The **D threshold** screw affects DOUT, not the analog classifier.
If input is stuck or near a rail, check the blue AOUT → D0 connection.

The older hard-gated analog classifier remains in `AudioClapDetector.h` for
replay comparison. Original DOUT-only mode remains selectable through
`config::useAnalogAudio = false`, using `config::detector` timing.

## Thread router behaviour

The pinned ESP32-C6 prebuilt SDK has **Full Thread Device (FTD)** support.
The firmware checks this at compile time, keeps the receiver on, and enables
router eligibility using the OpenThread API. With `LOG ON`, it logs the actual
role and eligibility every ten seconds, for example:

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

The **73 automated checks** retain the original 14 digital and 31 analog
checks, add 17 burst-classifier/protocol checks, and run eleven Python checks for
capture integrity and native replay. New signal fixtures exercise coloured
claps, FFT alignment, positive onset flux, reverberation, singles/triples,
echoes, tones, clipping, electrical spikes, slow peaks, sustained backgrounds,
pair consistency, weak interruptions, recovery, and sample-clock rollover.
Capture checks cover fragmented reads, CRC corruption, missing frames, clock
gaps/wrap, invalid features, incomplete sessions, and agreement with a native
C++ replay fixture. These are not recorded real-room accuracy measurements.

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

## Credits

Created and maintained by [crazynucfan](https://github.com/crazynucfan), with
assistance from [OpenAI Codex](https://openai.com/codex/) on firmware, tooling,
documentation, and testing. Physical hardware tests and room observations were
provided by crazynucfan.

## Sources

- [Seeed XIAO ESP32-C6 pinout](https://wiki.seeedstudio.com/xiao_esp32c6_getting_started/)
- [Waveshare sensor analog/digital outputs and adjustment controls](https://www.waveshare.com/wiki/Sound_Sensor)
- [Arduino ESP32 3.3.12 source](https://github.com/espressif/arduino-esp32/tree/3.3.12)
- [Matter Generic Switch API](https://docs.espressif.com/projects/arduino-esp32/en/latest/matter/ep_generic_switch.html)
- [Home Assistant Matter event handling](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/event.py)
- [ESP32-C6 continuous ADC driver](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c6/api-reference/peripherals/adc_continuous.html)
- [OpenThread router API](https://openthread.io/reference/group/api-thread-router)

Released under the [MIT licence](LICENSE).
