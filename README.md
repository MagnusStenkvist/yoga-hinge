# yoga-hinge

Tablet mode for the **Lenovo Yoga 3 Pro** on Linux, driven by the laptop's own hinge-angle sensor.

A small root service (`yoga-hinged`) reads the hinge angle from the machine's sensor hub and
drives a virtual `SW_TABLET_MODE` input switch. The desktop then does what it does on any
convertible: keyboard and touchpad switch off, and the on-screen keyboard is available. An
optional GNOME Shell extension shows the angle and lets you change the angle at which tablet
mode switches on.

**Tested only on:** Lenovo Yoga 3 Pro, Fedora 44, kernel 7.2.9, GNOME 50 on Wayland.
Nothing else has been tried.

Written with AI assistance (Claude); reverse-engineered and tested on real hardware by the author.

## Is this for your laptop?

It talks to one specific piece of hardware: the ITE sensor hub, USB `048D:8386`, and its
vendor sensor whose model string is `Lenovo Yoga` (HID feature/input report 7, usage `0x2000e1`).
Check before installing; this only reads, it writes nothing:

    sudo python3 daemon/yoga_hinged.py --check

It must end with `OK: this is the supported sensor`. If your machine already gets a tablet-mode
switch from the kernel, you don't need this, and a second switch could conflict:

    sudo libinput list-devices | grep -B6 -i "switches:.*tablet"

## Safety

The service writes two bytes (reporting state and power state) of the sensor's HID feature
report 7 to switch it on. It refuses to do so unless the report's "Sensor Model" string starts
with `Lenovo Yoga`, and it touches nothing else on the hub. It runs as root, and this is
reverse-engineered hobby software. **No warranty. Use at your own risk.**

## How it works

Fedora's kernel does not build `hid_sensor_custom`, so nothing consumes this sensor by default.
Once enabled it streams 15-byte HID input reports (found with `hid-recorder`):

| Bytes | Meaning |
|---|---|
| 0 | report id, `07` |
| 1 | sensor state, `02` = ready |
| 2 | event type: `03` initial sample, `04` data updated |
| 3–8 | three int16 LE, 0.001 g: a second accelerometer (base) |
| 9–10 | base pitch, 0.1° |
| 11–12 | display pitch, 0.1° |
| 13–14 | **hinge angle**, 0.1°, 0–360 (= display − base, mod 360) |

Feature report 7 (33 bytes): byte 2 reporting state, byte 3 power state (`1`/`1` = off,
`2`/`2` = on), bytes 11–32 the model string as eleven 16-bit characters. Closed (0°) and fully
folded back (360°) look identical to the sensor, so readings within 10° of 0/360 are resolved
from the previous angle; everything else is taken as is. Samples whose three angles disagree
are discarded. Tablet mode switches on at the threshold (default 200°) and off again below
threshold − hysteresis (default 10°).

## Requirements

* The hardware above, Linux with `hidraw` and `uinput`, systemd, and a D-Bus system bus.
* Python 3 with PyGObject (Fedora `python3-gobject-base`, Debian/Ubuntu `python3-gi`, Arch
  `python-gobject`). Only Fedora has been tried.
* For the extension: GNOME Shell 47–50 (seen running on 50 only), `glib-compile-schemas`.
* `--monitor` and `--check` need only the Python standard library.

## Install

    sudo ./install-daemon.sh        # service, D-Bus policy, loads uinput at boot
    ./install-extension.sh          # then log out and back in once
    gnome-extensions enable yoga-hinge@magnusstenkvist
    gnome-extensions prefs  yoga-hinge@magnusstenkvist

Try it without installing anything:

    sudo python3 daemon/yoga_hinged.py --monitor

It prints the live angle and whether tablet mode would be on, and restores the sensor on Ctrl-C.

Verify the installed service:

    journalctl -u yoga-hinged -f             # "tablet mode ON at 204.0 deg" as you fold
    sudo libinput debug-events | grep -i switch

Settings (threshold 150–350°, hysteresis 0–45°) live in the extension, or call
`org.yogahinge.Hinge1.SetThresholds` on the system bus. They are stored in
`/var/lib/yoga-hinge/config.ini`. Any local user may change them over D-Bus; see
`dbus/org.yogahinge.conf` if that matters to you. Set `YOGA_HINGE_DEBUG=1` to log every sample.

Remove: `sudo ./uninstall.sh`, and `gnome-extensions uninstall yoga-hinge@magnusstenkvist`.

## Known issues

* The sensor goes quiet for a second or two during fast folds, so switching can lag a quick flip.
* If the machine **boots** already folded fully back, it is taken as closed until the lid moves.
* Google Chrome 155 on GNOME/Wayland did not show the on-screen keyboard for web page text
  fields (GNOME apps and the lock screen did). That is a Chrome/GNOME interaction; swiping up
  from the bottom edge opens the keyboard by hand.
* SELinux: the service runs unconfined. If `ausearch -m avc -ts recent` shows hidraw or uinput
  denials, please open an issue with the lines.

## Development

    tests/run_all.sh

Replays two real recordings (`tests/data`) through the angle logic and exercises the D-Bus
interface on a private session bus. It needs no hardware, and the D-Bus part is skipped
without PyGObject.

## License

MIT, see [LICENSE](LICENSE).
