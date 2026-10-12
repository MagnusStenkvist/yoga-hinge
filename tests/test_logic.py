import struct, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "daemon"))
import yoga_hinged as y

def replay(path, thr=200, hyst=10):
    tr = y.HingeTracker(thr, hyst); events = []
    for l in open(path):
        p = l.split()
        if len(p) < 4: continue
        b = bytes(int(x, 16) for x in p[1:])
        d = y.decode_input(b)
        if d and d["consistent"] and tr.update(d["hinge"], d["initial"]):
            events.append((float(p[0]), round(tr.angle, 1), tr.tablet))
    return events, tr

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ev, tr = replay(os.path.join(D, "fold-to-270.log"))
print("fold-to-270 log, threshold 200:", ev)
assert [e[2] for e in ev] == [True, False], ev
assert 200 <= ev[0][1] < 215 and 185 <= ev[1][1] < 190
ev2, _ = replay(os.path.join(D, "messy-run.log"))
print("previous (messy) log:", ev2)
assert [e[2] for e in ev2] == [True, False]   # the sensor reports a real 299 deg sample at 21.9 s (that run was messy)
ev3, _ = replay(os.path.join(D, "messy-run.log"), thr=170, hyst=5)
print("same log at threshold 170:", ev3)
assert ev3 and ev3[0][2] is True

# unwrapping: physically impossible angles are never chosen, sparse samples are fine
t = y.HingeTracker(); t.update(109.0); t.update(337.0); assert t.tablet and t.angle == 337.0   # 2 s sensor gap, big jump
for raw in (340, 335, 345, 354, 1, 7, 356, 0, 2, 357, 359, 0.4, 358): t.update(raw)
assert t.tablet and t.angle > 350, t.angle          # wobbling around fully-folded-back stays tablet
t.update(319); assert t.tablet                       # flex while folded back
t.update(136.0); assert not t.tablet and t.angle == 136.0   # fast fold back, one sample
t = y.HingeTracker(); t.update(100)
for raw in (60, 20, 5, 359.0, 357.0, 2.0, 0.0, 358.0): t.update(raw)
assert not t.tablet and t.angle < 10, t.angle        # closing the lid and wobbling around 0 is not tablet
t = y.HingeTracker(); t.update(358.0); assert t.angle < 0 and not t.tablet   # boot ambiguity -> closed
t = y.HingeTracker()
for raw in (100, 140, 180, 220, 260, 300, 340, 359.5, 0.4, 359.0): t.update(raw)
assert t.tablet and t.angle > 355, t.angle
# replay of the debug run you sent: tablet must switch ON at the 337 sample and OFF at 136
seq = [109,161,337,340,335,345,354,1,7,356,0,2,357,1,3,357,0,359,0,358,1,358,0,358,0,359,0,358,0,358,1,359,357,0,354,319,352,136,131,98,102]
t = y.HingeTracker(); ons = []
for i, raw in enumerate(seq):
    if t.update(raw): ons.append((i, raw, t.tablet))
assert ons == [(2, 337, True), (37, 136, False)], ons
# hysteresis
t = y.HingeTracker(200, 10)
for raw, want in ((199, False), (200, True), (195, True), (190, True), (189.9, False), (199, False)):
    t.update(raw); assert t.tablet == want, (raw, t.tablet)
# threshold change re-evaluates
t = y.HingeTracker(200, 10); t.update(210); assert t.tablet
t.set_thresholds(250, 10); assert not t.tablet
# validation
for bad in ((100, 5), (351, 5), (200, 46), (160, 45)):
    try: y.validate_thresholds(*bad); raise SystemExit("accepted %r" % (bad,))
    except ValueError: pass
y.validate_thresholds(200, 10); y.validate_thresholds(350, 0); y.validate_thresholds(150, 30)
# decode: sample from the log
b = bytes.fromhex("07 02 04 f5 ff 13 00 1c fc 00 00 aa 0a a0 0a".replace(" ", ""))
d = y.decode_input(b); assert abs(d["hinge"]-272.0)<.01 and d["base"]==0 and abs(d["display"]-273.0)<.01, d
assert y.decode_input(bytes.fromhex("0101020000")) is None and y.decode_input(b[:10]) is None
# model-string safety check, using the feature report dumped from the real sensor
feat = bytes.fromhex("07 01 01 01 02 64 00 00 00 01 00 4c 00 65 00 6e 00 6f 00 76 00 6f 00 20 00 59 00 6f 00 67 00 61 00".replace(" ", ""))
assert y.model_string(feat) == "Lenovo Yoga" and y.model_string(feat).startswith(y.MODEL_PREFIX)
other = bytearray(feat); other[11:13] = b"X\x00"
assert not y.model_string(other).startswith(y.MODEL_PREFIX)
assert y.model_string(bytes(33)) == ""   # all zero -> refuse
# uinput struct sizes
assert struct.calcsize("80sHHHHI256i") == 1116 and struct.calcsize("llHHi") == 24
# config
import tempfile
p = os.path.join(tempfile.mkdtemp(), "c.ini"); c = y.Config(p); assert (c.threshold, c.hysteresis) == (200, 10)
c.threshold, c.hysteresis = 215, 12; c.save(); c2 = y.Config(p); assert (c2.threshold, c2.hysteresis) == (215, 12)
open(p, "w").write("[hinge]\ntablet_threshold = 5\n"); c3 = y.Config(p); assert c3.threshold == 200
print("all logic tests passed")
