#!/bin/sh
# Run everything that does not need the real hardware.
set -e
cd "$(dirname "$0")/.."
python3 tests/test_logic.py
if python3 -c 'import gi; gi.require_version("Gio","2.0")' 2>/dev/null && command -v dbus-run-session >/dev/null; then
    dbus-run-session -- sh tests/dbus_test.sh
else
    echo "skipping D-Bus test (needs PyGObject and dbus-run-session)"
fi
