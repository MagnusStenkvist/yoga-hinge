#!/usr/bin/env python3
"""yoga-hinged: turn the Lenovo Yoga 3 Pro hinge angle into SW_TABLET_MODE.

The ITE sensor hub (USB 048D:8386) has a vendor "custom" HID sensor (report 7,
usage 0x2000e1, model string "Lenovo Yoga").  Once its reporting/power state
is set to 2/2 it streams:

    byte 0   report id (7)
    byte 1   sensor state (2 = ready)
    byte 2   event type (3 = initial, 4 = data updated)
    byte 3.. six little-endian int16:
             1-3  second accelerometer, 0.001 g
             4    base pitch,    0.1 deg
             5    display pitch, 0.1 deg
             6    hinge angle,   0.1 deg  (= value5 - value4 mod 360)

The daemon enables the sensor, tracks the hinge angle (unwrapping the 0/360
ambiguity from history), applies a threshold with hysteresis and drives a
virtual uinput device that carries SW_TABLET_MODE.  Threshold and hysteresis
are exposed on the system bus as org.yogahinge so a GNOME extension can
change them.

Safety: feature report 7 is only ever written when its "Sensor Model" string
starts with "Lenovo Yoga" (see MODEL_PREFIX).  Nothing else on the hub is touched.

Run with --check to test for the sensor without changing anything, or with
--monitor to just print the angle (no uinput, no D-Bus).
"""

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import configparser
import fcntl
import glob
import os
import select
import signal
import struct
import sys
import time

HID_ID_MATCH = "0000048D:00008386"  # ITE Tech. sensor hub
RID_CUSTOM = 7
# Feature report 7 carries a "Sensor Model" string in 16-bit words from byte 11.
# We only write to it if that string says this is the sensor we reverse-engineered.
MODEL_OFFSET = 11
MODEL_PREFIX = "Lenovo Yoga"
FEATURE_LEN = 33
INPUT_LEN = 15
ENABLED_STATE = 2  # "Report All Events" / "D0 Full Power" (index + 1)

DEFAULT_THRESHOLD = 200
DEFAULT_HYSTERESIS = 10
CONSISTENCY_DEG = 5.0   # |(display - base) - hinge| allowed
AMBIGUOUS_DEG = 10.0    # readings this close to 0/360 need history to resolve
THRESHOLD_RANGE = (150, 350)
HYSTERESIS_RANGE = (0, 45)

BUS_NAME = "org.yogahinge"
OBJ_PATH = "/org/yogahinge/Hinge"
IFACE = "org.yogahinge.Hinge1"

# linux/uinput.h, linux/input-event-codes.h
UI_SET_EVBIT = 0x40045564
UI_SET_SWBIT = 0x4004556D
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EV_SYN, EV_SW = 0x00, 0x05
SYN_REPORT = 0
SW_TABLET_MODE = 0x01
BUS_VIRTUAL = 0x06


DEBUG = os.environ.get("YOGA_HINGE_DEBUG") == "1"


def log(*a):
    print(*a, flush=True)


def dlog(*a):
    if DEBUG:
        print("%.3f" % time.monotonic(), *a, flush=True)


# ---------------------------------------------------------------- pure logic

def decode_input(buf):
    """Decode a report-7 input report, or None if it is not one."""
    if len(buf) < INPUT_LEN or buf[0] != RID_CUSTOM:
        return None
    if buf[1] != 2 or buf[2] not in (3, 4):
        return None
    v = struct.unpack_from("<hhhhhh", buf, 3)
    base, display, hinge = ((x / 10.0) % 360.0 for x in v[3:])
    # The three angles are not always refreshed together (one sample in ~200
    # in the captures had a stale hinge value); flag those so they are skipped.
    skew = ((display - base) - hinge + 180.0) % 360.0 - 180.0
    return {
        "accel": tuple(x / 1000.0 for x in v[:3]),
        "base": base,
        "display": display,
        "hinge": hinge,
        "initial": buf[2] == 3,
        "consistent": abs(skew) <= CONSISTENCY_DEG,
    }


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


class HingeTracker:
    """Map the raw 0..360 hinge reading to a physical angle and decide tablet mode.

    Closed (0 deg) and fully folded back (360 deg) are the same rotation, so
    the sensor cannot tell them apart there.  Everywhere else it can: a lid
    cannot go below closed, so e.g. a raw 337 is 337 and not -23, however far
    it is from the previous sample (the sensor goes silent during fast folds
    and then jumps).  Only readings within AMBIGUOUS_DEG of 0/360 need history:
    if the last known angle was past 180 we are on the 360 side, else on the
    closed side.  On the very first sample, ambiguous readings count as closed.
    """

    def __init__(self, threshold=DEFAULT_THRESHOLD, hysteresis=DEFAULT_HYSTERESIS):
        self.threshold = threshold
        self.hysteresis = hysteresis
        self.angle = None
        self.tablet = False

    def set_thresholds(self, threshold, hysteresis):
        self.threshold, self.hysteresis = threshold, hysteresis
        self._decide()

    def update(self, raw, trusted=False):
        """Feed one raw hinge sample; returns True if tablet state changed."""
        if AMBIGUOUS_DEG < raw < 360.0 - AMBIGUOUS_DEG:
            cand = raw
        else:
            near_360 = self.angle is not None and self.angle >= 180.0
            if near_360:
                cand = raw if raw >= 180.0 else raw + 360.0
            else:
                cand = raw - 360.0 if raw >= 180.0 else raw
        self.angle = clamp(cand, -AMBIGUOUS_DEG, 360.0 + AMBIGUOUS_DEG)
        return self._decide()

    def _decide(self):
        """Returns True if the tablet state changed."""
        if self.angle is None:
            return False
        old = self.tablet
        if not self.tablet and self.angle >= self.threshold:
            self.tablet = True
        elif self.tablet and self.angle < self.threshold - self.hysteresis:
            self.tablet = False
        return self.tablet != old


def validate_thresholds(threshold, hysteresis):
    if not THRESHOLD_RANGE[0] <= threshold <= THRESHOLD_RANGE[1]:
        raise ValueError("threshold must be %d..%d" % THRESHOLD_RANGE)
    if not HYSTERESIS_RANGE[0] <= hysteresis <= HYSTERESIS_RANGE[1]:
        raise ValueError("hysteresis must be %d..%d" % HYSTERESIS_RANGE)
    if threshold - hysteresis < 120:
        raise ValueError("threshold - hysteresis must be at least 120")


class Config:
    def __init__(self, path):
        self.path = path
        self.threshold = DEFAULT_THRESHOLD
        self.hysteresis = DEFAULT_HYSTERESIS
        self.load()

    def load(self):
        cp = configparser.ConfigParser()
        try:
            cp.read(self.path)
            t = cp.getint("hinge", "tablet_threshold", fallback=DEFAULT_THRESHOLD)
            h = cp.getint("hinge", "hysteresis", fallback=DEFAULT_HYSTERESIS)
            validate_thresholds(t, h)
            self.threshold, self.hysteresis = t, h
        except (configparser.Error, ValueError, OSError) as e:
            log("config: ignoring %s (%s), using defaults" % (self.path, e))

    def save(self):
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.write("[hinge]\ntablet_threshold = %d\nhysteresis = %d\n"
                    % (self.threshold, self.hysteresis))
        os.replace(tmp, self.path)


# -------------------------------------------------------------- hardware I/O

def _ioc(nr, n):
    # _IOC(_IOC_READ|_IOC_WRITE, 'H', nr, n)
    return (3 << 30) | (n << 16) | (ord("H") << 8) | nr


def get_feature(fd, rid, n):
    b = bytearray(n)
    b[0] = rid
    fcntl.ioctl(fd, _ioc(0x07, n), b)  # HIDIOCGFEATURE
    return b


def set_feature(fd, b):
    fcntl.ioctl(fd, _ioc(0x06, len(b)), bytes(b))  # HIDIOCSFEATURE


def model_string(feature):
    """Decode the 'Sensor Model' property of feature report 7 (UTF-16LE-ish)."""
    words = struct.unpack_from("<%dH" % ((len(feature) - MODEL_OFFSET) // 2),
                               bytes(feature), MODEL_OFFSET)
    return "".join(chr(w) for w in words if 32 <= w < 127)


_warned = set()


def warn_once(msg):
    if msg not in _warned:
        _warned.add(msg)
        log(msg)


def find_hidraw():
    """Return an open fd for the sensor hub node that has feature report 7,
    and whose model string matches MODEL_PREFIX."""
    for u in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            if HID_ID_MATCH not in open(u).read().upper():
                continue
        except OSError:
            continue
        node = "/dev/" + u.split("/")[4]
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            log("cannot open %s: %s" % (node, e))
            continue
        try:
            r = get_feature(fd, RID_CUSTOM, FEATURE_LEN)
            if r[0] == RID_CUSTOM:
                model = model_string(r)
                if model.startswith(MODEL_PREFIX):
                    return fd, node
                warn_once("%s: sensor model %r is not %r, leaving it alone"
                          % (node, model, MODEL_PREFIX))
        except OSError:
            pass
        os.close(fd)
    return None, None


class Sensor:
    """The custom sensor on the hub: enable it, read reports."""

    def __init__(self):
        self.fd = None
        self.orig = None
        self.node = None

    def open(self):
        fd, node = find_hidraw()
        if fd is None:
            return False
        self.fd, self.node = fd, node
        self.orig = get_feature(fd, RID_CUSTOM, FEATURE_LEN)
        self.ensure_enabled()
        log("sensor: using %s, original state bytes %d/%d"
            % (node, self.orig[2], self.orig[3]))
        return True

    def ensure_enabled(self):
        """Idempotent; returns True if it had to (re)enable the sensor."""
        cur = get_feature(self.fd, RID_CUSTOM, FEATURE_LEN)
        if cur[2] == ENABLED_STATE and cur[3] == ENABLED_STATE:
            return False
        new = bytearray(cur)
        new[2] = new[3] = ENABLED_STATE
        set_feature(self.fd, new)
        return True

    def close(self, restore=True):
        if self.fd is None:
            return
        try:
            if restore and self.orig is not None:
                cur = get_feature(self.fd, RID_CUSTOM, FEATURE_LEN)
                cur[2], cur[3] = self.orig[2], self.orig[3]
                set_feature(self.fd, cur)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None


class TabletSwitch:
    """Virtual input device that only carries SW_TABLET_MODE."""

    def __init__(self):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SW)
        fcntl.ioctl(self.fd, UI_SET_SWBIT, SW_TABLET_MODE)
        dev = struct.pack("80sHHHHI256i", b"Yoga hinge tablet-mode switch",
                          BUS_VIRTUAL, 0x17EF, 0x0001, 1, 0, *([0] * 256))
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def set(self, on):
        now = time.time()
        sec, usec = int(now), int((now % 1) * 1e6)
        ev = struct.pack("llHHi", sec, usec, EV_SW, SW_TABLET_MODE, 1 if on else 0)
        ev += struct.pack("llHHi", sec, usec, EV_SYN, SYN_REPORT, 0)
        os.write(self.fd, ev)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


class _NullSwitch:
    def set(self, on):
        pass

    def close(self):
        pass


# -------------------------------------------------------------------- modes

def monitor():
    s = Sensor()
    if not s.open():
        sys.exit("sensor hub not found (048D:8386)")
    tr = HingeTracker()
    stop = []
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    last = None
    log("monitoring; Ctrl-C to stop (sensor is restored on exit)")
    try:
        while not stop:
            if not select.select([s.fd], [], [], 0.5)[0]:
                continue
            d = decode_input(os.read(s.fd, 64))
            if not d:
                continue
            if not d["consistent"]:
                continue
            tr.update(d["hinge"], d["initial"])
            line = "hinge %6.1f  base %6.1f  display %6.1f  tablet@%d: %s" % (
                tr.angle, d["base"], d["display"], tr.threshold,
                "YES" if tr.tablet else "no")
            if line != last:
                log(line)
                last = line
    finally:
        s.close()


def check():
    """Read-only: is the sensor present and is it the one we know?"""
    for u in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            if HID_ID_MATCH not in open(u).read().upper():
                continue
        except OSError:
            continue
        node = "/dev/" + u.split("/")[4]
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
            r = get_feature(fd, RID_CUSTOM, FEATURE_LEN)
            os.close(fd)
        except OSError as e:
            log("%s: cannot read feature report %d: %s" % (node, RID_CUSTOM, e))
            continue
        model = model_string(r)
        log("%s: report %d model %r, state bytes %d/%d"
            % (node, RID_CUSTOM, model, r[2], r[3]))
        if model.startswith(MODEL_PREFIX):
            log("OK: this is the supported sensor")
            return 0
        log("NOT supported: model string does not start with %r" % MODEL_PREFIX)
        return 1
    log("sensor hub %s not found" % HID_ID_MATCH)
    return 1


def run_daemon():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    state_dir = os.environ.get("STATE_DIRECTORY", "/var/lib/yoga-hinge")
    cfg = Config(os.path.join(state_dir, "config.ini"))
    tracker = HingeTracker(cfg.threshold, cfg.hysteresis)
    sensor = Sensor()
    test_mode = os.environ.get("YOGA_HINGE_TEST") == "1"  # no uinput, session bus
    switch = _NullSwitch() if test_mode else TabletSwitch()
    loop = GLib.MainLoop()
    st = {"conn": None, "watch": None, "shown_angle": None, "present": False}

    node_info = Gio.DBusNodeInfo.new_for_xml("""
    <node><interface name="%s">
      <property name="Angle" type="d" access="read"/>
      <property name="TabletMode" type="b" access="read"/>
      <property name="Threshold" type="u" access="read"/>
      <property name="Hysteresis" type="u" access="read"/>
      <property name="SensorPresent" type="b" access="read"/>
      <method name="SetThresholds">
        <arg type="u" name="threshold" direction="in"/>
        <arg type="u" name="hysteresis" direction="in"/>
      </method>
    </interface></node>""" % IFACE)

    def prop(name):
        a = tracker.angle if tracker.angle is not None else -1.0
        return {
            "Angle": GLib.Variant("d", round(a, 1)),
            "TabletMode": GLib.Variant("b", tracker.tablet),
            "Threshold": GLib.Variant("u", tracker.threshold),
            "Hysteresis": GLib.Variant("u", tracker.hysteresis),
            "SensorPresent": GLib.Variant("b", st["present"]),
        }[name]

    def notify(*names):
        conn = st["conn"]
        if conn is None:
            return
        changed = {n: prop(n) for n in names}
        conn.emit_signal(None, OBJ_PATH, "org.freedesktop.DBus.Properties",
                         "PropertiesChanged",
                         GLib.Variant("(sa{sv}as)", (IFACE, changed, [])))

    def apply_tablet():
        switch.set(tracker.tablet)
        log("tablet mode %s at %.1f deg" % ("ON" if tracker.tablet else "off",
                                            tracker.angle))
        notify("TabletMode")

    def on_method(conn, sender, path, iface, method, params, inv):
        if method == "SetThresholds":
            t, h = params.unpack()
            try:
                validate_thresholds(t, h)
            except ValueError as e:
                inv.return_dbus_error("org.yogahinge.Error.InvalidArgs", str(e))
                return
            cfg.threshold, cfg.hysteresis = t, h
            try:
                cfg.save()
            except OSError as e:
                log("cannot save config: %s" % e)
            was = tracker.tablet
            tracker.set_thresholds(t, h)
            log("thresholds set to %d / %d by %s" % (t, h, sender))
            notify("Threshold", "Hysteresis")
            if tracker.tablet != was:
                apply_tablet()
            inv.return_value(None)
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def on_bus(conn, name):
        st["conn"] = conn
        conn.register_object(OBJ_PATH, node_info.interfaces[0], on_method,
                             lambda c, s, p, i, n: prop(n), None)

    bus = Gio.BusType.SESSION if test_mode else Gio.BusType.SYSTEM
    Gio.bus_own_name(bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     on_bus, None, lambda *_: log("lost bus name"))

    def on_readable(fd, cond):
        if cond & (GLib.IO_ERR | GLib.IO_HUP):
            log("sensor: device went away")
            drop_sensor()
            return False
        try:
            data = os.read(fd, 64)
        except BlockingIOError:
            return True
        except OSError:
            drop_sensor()
            return False
        d = decode_input(data)
        if d:
            dlog("sample hinge %.1f base %.1f display %.1f initial=%s consistent=%s"
                 % (d["hinge"], d["base"], d["display"], d["initial"], d["consistent"]))
        if d and d["consistent"]:
            changed = tracker.update(d["hinge"], d["initial"])
            dlog("  -> angle %.1f tablet=%s" % (tracker.angle, tracker.tablet))
            if changed:
                apply_tablet()
            shown = round(tracker.angle)
            if shown != st["shown_angle"]:
                st["shown_angle"] = shown
                notify("Angle")
        return True

    def drop_sensor():
        if st["watch"]:
            GLib.source_remove(st["watch"])
            st["watch"] = None
        sensor.close(restore=False)
        st["present"] = False
        notify("SensorPresent")

    def try_open():
        if sensor.fd is not None:
            return
        try:
            ok = sensor.open()
        except OSError as e:
            log("sensor: open failed: %s" % e)
            sensor.close(restore=False)
            return
        if ok:
            st["present"] = True
            st["watch"] = GLib.io_add_watch(
                sensor.fd, GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP, on_readable)
            notify("SensorPresent")

    def housekeeping():
        """Re-open after unplug / resume and keep the sensor enabled."""
        if sensor.fd is None:
            try_open()
            return True
        try:
            if sensor.ensure_enabled():
                log("sensor: was disabled (resume?), re-enabled")
        except OSError as e:
            log("sensor: housekeeping failed: %s" % e)
            drop_sensor()
        return True

    def shutdown(*_):
        loop.quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    try_open()
    GLib.timeout_add_seconds(5, housekeeping)
    log("yoga-hinged running, threshold %d hysteresis %d"
        % (tracker.threshold, tracker.hysteresis))
    try:
        loop.run()
    finally:
        sensor.close(restore=True)
        try:
            switch.set(False)
        finally:
            switch.close()


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    elif "--monitor" in sys.argv:
        monitor()
    else:
        run_daemon()
