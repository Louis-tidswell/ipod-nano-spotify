"""Local addresses shared by the web page and Nano mailbox bridge."""
import ipaddress
import json
import subprocess
from pathlib import Path

CONFIG_DIR = Path.home() / '.config/ipod-nano-config'


def addresses():
    result = subprocess.run(['ip', '-j', '-4', 'address', 'show', 'up'],
                            capture_output=True, text=True, timeout=3, check=True)
    found = []
    for link in json.loads(result.stdout):
        name = link['ifname']
        if name == 'lo' or not (name.startswith(('wl', 'en', 'eth'))):
            continue
        for address in link.get('addr_info', []):
            ip = address.get('local', '')
            if address.get('scope') == 'global' and not ipaddress.ip_address(ip).is_loopback:
                found.append({'interface': name, 'ip': ip, 'wifi': name.startswith('wl')})
    return sorted(found, key=lambda row: (not row['wifi'], row['interface'], row['ip']))


def config_url():
    try:
        settings = json.loads((CONFIG_DIR / 'settings.json').read_text())
        port = int(settings.get('port', 8080))
    except (OSError, ValueError):
        port = 8080
    try:
        rows = addresses()
        return f"http://{rows[0]['ip']}:{port}" if rows else 'Connect Pi to Wi-Fi'
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'Checking Pi network...'
