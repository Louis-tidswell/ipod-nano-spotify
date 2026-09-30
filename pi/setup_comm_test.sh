#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
nanoapps_dir=${NANOAPPS_DIR:-"$HOME/NanoApps"}

if [ ! -x "$nanoapps_dir/start" ]; then
    echo "NanoApps was not found at $nanoapps_dir" >&2
    exit 1
fi

echo "Installing the iPod-specific udev permission rule..."
sudo install -m 0644 "$project_dir/pi/99-ipod-comms.rules" /etc/udev/rules.d/99-ipod-comms.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=change --subsystem-match=block
sudo udevadm trigger --action=change --subsystem-match=scsi_generic

echo "Building and installing Pi Link Test..."
mkdir -p "$nanoapps_dir/apps/comms_test"
cp "$project_dir/nanoapps/comms_test/Makefile" \
   "$project_dir/nanoapps/comms_test/Info.plist" \
   "$project_dir/nanoapps/comms_test/comms_test.c" \
   "$nanoapps_dir/apps/comms_test/"
(cd "$nanoapps_dir" && ./start install comms_test)

echo "Enabling the per-user Pi bridge service..."
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 "$project_dir/pi/ipod-comm-test.service" \
    "$HOME/.config/systemd/user/ipod-comm-test.service"
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now ipod-comm-test.service

echo
echo "Setup complete. Open 'Pi Link Test' on the iPod and tap SEND TEST."
echo "Live bridge log: journalctl --user -u ipod-comm-test.service -f"
