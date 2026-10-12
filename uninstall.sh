#!/bin/sh
# sudo ./uninstall.sh   (the extension: gnome-extensions uninstall yoga-hinge@magnusstenkvist)
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl disable --now yoga-hinged.service 2>/dev/null
rm -f /etc/systemd/system/yoga-hinged.service /usr/local/libexec/yoga-hinged \
      /etc/dbus-1/system.d/org.yogahinge.conf /etc/modules-load.d/yoga-hinge.conf
systemctl daemon-reload
echo "removed (config left in /var/lib/yoga-hinge)"
