set -e
# Needs PyGObject for the interpreter in $PYTHON (default python3) and a D-Bus session bus:
#   dbus-run-session -- sh tests/dbus_test.sh
D=$(mktemp -d)
export STATE_DIRECTORY=$D YOGA_HINGE_TEST=1
${PYTHON:-python3} daemon/yoga_hinged.py > $D/log 2>&1 &
P=$!
sleep 1.5
call() { gdbus call --session --dest org.yogahinge --object-path /org/yogahinge/Hinge "$@"; }
get() { call --method org.freedesktop.DBus.Properties.Get org.yogahinge.Hinge1 "$1"; }
echo "Threshold:  $(get Threshold)"
echo "Hysteresis: $(get Hysteresis)"
echo "Present:    $(get SensorPresent)"
gdbus monitor --session --dest org.yogahinge > $D/mon 2>&1 &
M=$!
sleep 0.5
call --method org.yogahinge.Hinge1.SetThresholds 215 12 && echo "set 215/12 ok"
sleep 0.3
echo "Threshold now: $(get Threshold)"
echo "config file:"; cat $D/config.ini
echo "--- invalid:"
call --method org.yogahinge.Hinge1.SetThresholds 100 5 2>&1 | tail -1 || true
call --method org.yogahinge.Hinge1.SetThresholds 200 60 2>&1 | tail -1 || true
echo "Threshold still: $(get Threshold)"
kill $M; kill -TERM $P; wait $P 2>/dev/null || true
echo "--- signals seen:"; grep -c PropertiesChanged $D/mon || true
echo "--- restart reads saved config:"
${PYTHON:-python3} daemon/yoga_hinged.py > $D/log2 2>&1 &
P=$!; sleep 1.5; echo "Threshold after restart: $(get Threshold)"; kill -TERM $P; wait $P 2>/dev/null || true
echo "--- daemon log:"; cat $D/log $D/log2
