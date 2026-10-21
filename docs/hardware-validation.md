# Hardware validation

These checks require the physical XIAO, sound sensor, and the user's Home
Assistant/Thread network. They have **not yet been run**. Record the firmware
commit, Home Assistant version, Border Router, sensor settings, and outcome
when performing them. Do not include Thread credentials or device storage.

- [ ] Wiring matches DOUT → D1/GPIO1, VCC → 3V3, GND → GND.
- [ ] USB upload succeeds; normal boot shows a Generic Switch endpoint and pairing code.
- [ ] The device starts on USB power without a connected serial monitor.
- [ ] DOUT is normally HIGH; one nearby clap produces one accepted burst.
- [ ] Single clap produces no Home Assistant action.
- [ ] Double clap produces exactly one action after the recognition window.
- [ ] Triple clap produces no action.
- [ ] Quiet room, conversation, TV, and a door closing are evaluated for false triggers.
- [ ] BLE commissioning joins the existing Thread network; the event entity appears.
- [ ] Serial output reports router eligibility and the actual Thread role.
- [ ] Unplug/replug retains pairing and the automation still works.
- [ ] Events during a network outage are discarded and not replayed on reconnect.
- [ ] Holding BOOT for five seconds resets pairing and allows commissioning again.

## Results

Pending physical testing.
