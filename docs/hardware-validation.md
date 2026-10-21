# Hardware validation

These checks require the physical XIAO, sound sensor, and the user's Home
Assistant/Thread network. Flashing, startup, and commissioning have been checked;
the remaining checks are pending. Record the firmware commit, Home Assistant
version, Border Router, sensor settings, and outcome when performing them.
Do not include Thread credentials or device storage.

- [ ] Wiring matches DOUT → D1/GPIO1, VCC → 3V3, GND → GND.
- [x] USB upload succeeds; normal boot shows a Generic Switch endpoint and pairing code.
- [ ] The device starts on USB power without a connected serial monitor.
- [ ] DOUT is normally HIGH; one nearby clap produces one accepted burst.
- [ ] Single clap produces no Home Assistant action.
- [x] Two double-clap sequences reach the Matter click API in the serial log.
- [ ] Double clap produces exactly one action after the recognition window.
- [ ] Triple clap produces no action.
- [ ] Quiet room, conversation, TV, and a door closing are evaluated for false triggers.
- [x] Commissioning into Home Assistant joins the existing Thread network.
- [ ] The Generic Switch event entity appears and updates in Home Assistant.
- [x] Serial output reports router eligibility and the actual Thread role.
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
  `role=child router_eligible=yes config_error=0`. Router promotion is pending;
  an attached child role is normal for a router-eligible device.
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
