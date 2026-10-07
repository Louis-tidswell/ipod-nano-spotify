#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
account=$(id -un)
if [[ "$account" == root ]]; then
  echo 'Run this as the normal Pi user, with sudo available.' >&2
  exit 1
fi
if [[ ! "$account" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]; then
  echo 'Unsupported Pi username' >&2
  exit 1
fi
sudo install -d -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0555 "$repo_root/pi/config_helper.py" /usr/local/libexec/ipod-nano-config-helper
rule_file=$(mktemp)
trap 'rm -f "$rule_file"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/ipod-nano-config-helper ""\n' "$account" > "$rule_file"
sudo visudo -cf "$rule_file"
sudo install -o root -g root -m 0440 "$rule_file" /etc/sudoers.d/ipod-nano-config
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 "$repo_root/pi/ipod-nano-config.service" "$HOME/.config/systemd/user/"
install -m 0644 "$repo_root/pi/ipod-nano-boot-reload.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
# Enable for the next Pi boot. Do not reload an in-use Nano during setup.
systemctl --user enable ipod-nano-boot-reload.service
systemctl --user enable --now ipod-nano-config.service
systemctl --user restart ipod-nano-config.service
sudo loginctl enable-linger "$account"
for attempt in {1..30}; do
  if [[ -f "$HOME/.config/ipod-nano-config/setup-link.txt" ]]; then
    echo 'Open this one-time setup link to set your household password:'
    cat "$HOME/.config/ipod-nano-config/setup-link.txt"
    exit 0
  fi
  if [[ -f "$HOME/.config/ipod-nano-config/password.json" ]]; then
    echo 'Config page started. Your existing household password is unchanged.'
    exit 0
  fi
  sleep 1
done
echo 'Check: systemctl --user status ipod-nano-config.service' >&2
exit 1
