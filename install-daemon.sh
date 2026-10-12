#!/bin/sh
# Install the yoga-hinged service.  Run as root:  sudo ./install-daemon.sh
set -e
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")"

if ! python3 -c 'import gi; gi.require_version("Gio", "2.0")' 2>/dev/null; then
    echo "Python's PyGObject is missing. Install it first:"
    echo "  Fedora/RHEL:    sudo dnf install python3-gobject-base"
    echo "  Debian/Ubuntu:  sudo apt install python3-gi gir1.2-glib-2.0"
    echo "  Arch:           sudo pacman -S python-gobject"
    exit 1
fi
command -v systemctl >/dev/null || { echo "systemd is required"; exit 1; }

# Refuse early if the sensor is not the one this was written for.
echo "Checking the sensor..."
python3 daemon/yoga_hinged.py --check || {
    echo "Sensor check failed; not installing (see message above)."; exit 1; }

install -Dm755 daemon/yoga_hinged.py /usr/local/libexec/yoga-hinged
command -v restorecon >/dev/null && restorecon -v /usr/local/libexec/yoga-hinged || true
install -Dm644 systemd/yoga-hinged.service /etc/systemd/system/yoga-hinged.service
install -Dm644 dbus/org.yogahinge.conf /etc/dbus-1/system.d/org.yogahinge.conf
echo uinput > /etc/modules-load.d/yoga-hinge.conf
modprobe uinput || true
systemctl reload dbus 2>/dev/null || systemctl reload dbus-broker 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now yoga-hinged.service
sleep 1
systemctl --no-pager --lines=15 status yoga-hinged.service || true
