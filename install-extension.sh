#!/bin/sh
# Install the GNOME Shell extension for the current user:  ./install-extension.sh
set -e
cd "$(dirname "$0")"
UUID=yoga-hinge@magnusstenkvist
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"
if ! command -v glib-compile-schemas >/dev/null; then
    echo "glib-compile-schemas is missing:"
    echo "  Fedora:         sudo dnf install glib2"
    echo "  Debian/Ubuntu:  sudo apt install libglib2.0-bin"
    echo "  Arch:           sudo pacman -S glib2"
    exit 1
fi
mkdir -p "$DEST"
cp -r extension/. "$DEST/"
glib-compile-schemas "$DEST/schemas"
echo "Installed to $DEST"
echo "Log out and back in once (GNOME on Wayland only discovers new or changed"
echo "extensions at login), then:"
echo "  gnome-extensions enable $UUID"
echo "  gnome-extensions prefs  $UUID"
