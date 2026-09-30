#!/bin/sh
set -eu

version=${GO_LIBRESPOT_VERSION:-v0.10.2}
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
archive="go-librespot_linux_arm64.tar.gz"
url="https://github.com/devgianlu/go-librespot/releases/download/$version/$archive"
temp_dir=$(mktemp -d)
trap 'rm -rf "$temp_dir"' EXIT

echo "Installing go-librespot $version..."
curl -fL "$url" -o "$temp_dir/$archive"
tar -xzf "$temp_dir/$archive" -C "$temp_dir"
mkdir -p "$HOME/.local/bin" "$HOME/.config/go-librespot" "$HOME/.config/systemd/user"
install -m 0755 "$temp_dir/go-librespot" "$HOME/.local/bin/go-librespot"

echo "Installing the iPod SCSI permission rule..."
sudo install -m 0644 "$project_dir/pi/99-ipod-comms.rules" /etc/udev/rules.d/99-ipod-comms.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=change --subsystem-match=scsi_generic

if [ ! -f "$HOME/.config/go-librespot/config.yml" ]; then
    install -m 0600 "$project_dir/pi/go-librespot.yml" "$HOME/.config/go-librespot/config.yml"
else
    echo "Keeping existing ~/.config/go-librespot/config.yml"
fi

install -m 0644 "$project_dir/pi/go-librespot.service" "$HOME/.config/systemd/user/"
install -m 0644 "$project_dir/pi/ipod-spotify-bridge.service" "$HOME/.config/systemd/user/"
chmod +x "$project_dir/pi/spotify_bridge.py"
systemctl --user daemon-reload
systemctl --user disable --now ipod-comm-test.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/ipod-comm-test.service"
systemctl --user enable --now go-librespot.service ipod-spotify-bridge.service

echo
echo "go-librespot is installed. Retrieve the Spotify device code with:"
echo "  curl -s http://127.0.0.1:3678/auth/code"
echo "Then open the returned URL on your phone or computer and approve it."
