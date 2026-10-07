#!/usr/bin/python3 -I
"""Root-owned helper: fixed Pi controls, NetworkManager and iPod disk writes.

Installed outside the user-writable repository. No shell commands or arbitrary
filesystem paths are accepted from the web process.
"""
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

RUNTIME = Path('/run/ipod-nano-config')
CHECKPOINT = RUNTIME / 'checkpoint'


def run(arguments, check=True, timeout=45):
    result = subprocess.run(arguments, check=check, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip()


def split_fields(line):
    fields, value, escaped = [], '', False
    for char in line:
        if escaped:
            value += char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(value)
            value = ''
        else:
            value += char
    fields.append(value)
    return fields


def wifi_device(request):
    interface = request.get('interface', 'wlan0')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}', interface):
        raise ValueError('Invalid Wi-Fi interface')
    if not (Path('/sys/class/net') / interface / 'wireless').is_dir():
        raise ValueError('Choose an available Wi-Fi interface')
    return interface


def nm_bus():
    import dbus
    bus = dbus.SystemBus()
    obj = bus.get_object('org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager')
    return bus, dbus.Interface(obj, 'org.freedesktop.NetworkManager')


def wifi_snapshot():
    networks = []
    output = run(['/usr/bin/nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY,IN-USE,DEVICE',
                  'device', 'wifi', 'list', '--rescan', 'no'])
    for line in output.splitlines():
        fields = split_fields(line)
        if len(fields) == 5 and fields[0]:
            ssid, signal, security, active, device = fields
            row = dict(ssid=ssid, signal=int(signal), security=security, active=active == '*', interface=device)
            if not any(n['ssid'] == ssid and n['interface'] == device for n in networks):
                networks.append(row)
    saved = []
    output = run(['/usr/bin/nmcli', '-t', '-f', 'NAME,UUID,TYPE', 'connection', 'show'])
    for line in output.splitlines():
        fields = split_fields(line)
        if len(fields) == 3 and fields[2] == '802-11-wireless':
            saved.append(dict(name=fields[0], uuid=fields[1]))
    pending = False
    if CHECKPOINT.exists():
        bus, manager = nm_bus()
        checkpoints = bus.get_object('org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager')
        import dbus
        active = dbus.Interface(checkpoints, 'org.freedesktop.DBus.Properties').Get(
            'org.freedesktop.NetworkManager', 'Checkpoints')
        pending = CHECKPOINT.read_text().strip() in [str(p) for p in active]
        if not pending:
            CHECKPOINT.unlink(missing_ok=True)
    interfaces = [p.name for p in Path('/sys/class/net').iterdir() if (p / 'wireless').is_dir()]
    return dict(networks=networks, saved=saved, interfaces=interfaces, confirmation_pending=pending)


def wifi_change(request):
    import dbus
    interface = wifi_device(request)
    bus, manager = nm_bus()
    device = manager.GetDeviceByIpIface(interface)
    checkpoint = manager.CheckpointCreate(dbus.Array([device], signature='o'), dbus.UInt32(120), dbus.UInt32(1))
    CHECKPOINT.write_text(str(checkpoint))
    try:
        if request['action'] == 'wifi-saved':
            identifier = request.get('uuid', '')
            if not re.fullmatch(r'[a-fA-F0-9-]{36}', identifier):
                raise ValueError('Invalid saved Wi-Fi network')
            run(['/usr/bin/nmcli', '--wait', '35', 'connection', 'up', 'uuid', identifier, 'ifname', interface])
        else:
            ssid, password = request.get('ssid', ''), request.get('password', '')
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
                raise ValueError('SSID must be between 1 and 32 bytes')
            if not isinstance(password, str) or (password and not (8 <= len(password) <= 63 or re.fullmatch(r'[0-9a-fA-F]{64}', password))):
                raise ValueError('Use a WPA password of 8–63 characters, or leave it empty for an open network')
            settings = {
                'connection': {'id': 'Nano ' + ssid, 'type': '802-11-wireless', 'autoconnect': True},
                '802-11-wireless': {'ssid': dbus.ByteArray(ssid.encode()), 'mode': 'infrastructure'},
                'ipv4': {'method': 'auto'}, 'ipv6': {'method': 'auto'},
            }
            if password:
                settings['802-11-wireless']['security'] = '802-11-wireless-security'
                settings['802-11-wireless-security'] = {'key-mgmt': 'wpa-psk', 'psk': password}
            _, active_connection = manager.AddAndActivateConnection(settings, device, dbus.ObjectPath('/'))
            obj = bus.get_object('org.freedesktop.NetworkManager', active_connection)
            props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                state = int(props.Get('org.freedesktop.NetworkManager.Connection.Active', 'State'))
                if state == 2:
                    break
                if state == 4:
                    raise ValueError('Wi-Fi connection failed')
                time.sleep(0.5)
            else:
                raise ValueError('Wi-Fi connection timed out')
        return {'message': 'Connected. Confirm from the new network within 120 seconds or the previous connection returns.'}
    except Exception:
        manager.CheckpointRollback(checkpoint)
        manager.CheckpointDestroy(checkpoint)
        CHECKPOINT.unlink(missing_ok=True)
        raise


def ipod_device():
    for generic in sorted(Path('/sys/class/scsi_generic').glob('sg*')):
        if 'ipod' not in (generic / 'device/model').read_text().lower():
            continue
        hardware = (generic / 'device').resolve()
        if not any((p / 'idVendor').exists() and (p / 'idVendor').read_text().strip() == '05ac'
                   and (p / 'idProduct').read_text().strip() == '1267' for p in hardware.parents):
            continue
        blocks = list((generic / 'device/block').iterdir())
        if blocks:
            return '/dev/' + blocks[0].name
    raise ValueError('Connect the iPod Nano to the Pi by USB first')


def validate_archive(data):
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(data, validate=True)))
    infos = archive.infolist()
    if not infos or len(infos) > 200 or sum(i.file_size for i in infos) > 32 * 1024 * 1024:
        raise ValueError('App payload is too large or empty')
    names = set()
    for item in infos:
        name = item.filename
        if name in names or not (name == 'Apps/AllApps.pack' or
                re.fullmatch(r'Apps/(Executables|Icons)/[^/\\\x00]{1,100}\.(hbapp|bin)', name)):
            raise ValueError('App payload contains an unexpected file')
        names.add(name)
        if name.split('/')[-1].startswith('.'):
            raise ValueError('Invalid app filename')
    if 'Apps/AllApps.pack' not in names:
        raise ValueError('App pack is missing')
    if archive.testzip() is not None:
        raise ValueError('App payload failed its integrity check')
    return archive


def nano_install(request):
    archive = validate_archive(request.get('archive', ''))
    device = ipod_device()
    part = device + '1'
    run(['/usr/bin/sg_start', device, '--loej'], check=False)
    deadline = time.monotonic() + 40
    while not Path(part).exists() and time.monotonic() < deadline:
        time.sleep(1)
    if not Path(part).exists():
        raise ValueError('Nano did not enter disk mode')
    if run(['/usr/sbin/blkid', '-s', 'TYPE', '-o', 'value', part]) != 'vfat':
        raise ValueError('Nano partition is not a FAT filesystem')
    run(['/usr/bin/umount', part], check=False)
    result = subprocess.run(['/usr/sbin/fsck.vfat', '-a', '-w', part], capture_output=True, timeout=45)
    if result.returncode not in (0, 1):
        raise ValueError('Nano FAT check failed; repair the disk before installing')
    mount = RUNTIME / 'mount'
    mount.mkdir(exist_ok=True, mode=0o700)
    run(['/usr/bin/mount', '-o', 'rw,flush,nosuid,nodev,noexec,umask=077', part, str(mount)])
    try:
        # FAT has no symlinks. Only replace application payloads; preserve Apps/Data.
        apps = mount / 'Apps'
        apps.mkdir(exist_ok=True)
        staged, backup = apps / '.config-new', apps / '.config-backup'
        for folder in (staged, backup):
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir()
        for info in archive.infolist():
            target = staged / info.filename.removeprefix('Apps/')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open('wb') as dest:
                shutil.copyfileobj(source, dest)
        os.sync()
        swapped = []
        try:
            for name in ('AllApps.pack', 'Executables', 'Icons'):
                source, target, original = staged / name, apps / name, backup / name
                if target.exists():
                    target.rename(original)
                swapped.append(name)
                if source.exists():
                    source.rename(target)
            os.sync()
        except Exception:
            for name in reversed(swapped):
                target, original = apps / name, backup / name
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
                if original.exists():
                    original.rename(target)
            raise
        shutil.rmtree(staged)
        shutil.rmtree(backup)
    finally:
        run(['/usr/bin/umount', str(mount)])
        archive.close()
    return {'message': 'Selected apps installed; registering the Nano home screen'}


def dispatch(request):
    action = request.get('action')
    if action == 'status':
        return {'available': True}
    if action in ('reboot', 'poweroff'):
        # Let HTTP return the accepted job before the network disappears.
        run(['/usr/bin/systemd-run', '--quiet', '--on-active=3s', '/usr/bin/systemctl', action])
        return {'message': 'Pi ' + action + ' scheduled'}
    if action == 'wifi-status':
        return wifi_snapshot()
    if action == 'wifi-scan':
        run(['/usr/bin/nmcli', 'device', 'wifi', 'rescan', 'ifname', wifi_device(request)])
        time.sleep(2)
        return wifi_snapshot()
    if action in ('wifi-connect', 'wifi-saved'):
        return wifi_change(request)
    if action == 'wifi-confirm':
        if CHECKPOINT.exists():
            _, manager = nm_bus()
            try:
                manager.CheckpointDestroy(CHECKPOINT.read_text().strip())
            except Exception:
                # Timeout rollback may already have removed the checkpoint.
                pass
            CHECKPOINT.unlink(missing_ok=True)
        return {'message': 'Wi-Fi connection confirmed'}
    if action == 'wifi-forget':
        identifier = request.get('uuid', '')
        if not re.fullmatch(r'[a-fA-F0-9-]{36}', identifier):
            raise ValueError('Invalid saved Wi-Fi network')
        run(['/usr/bin/nmcli', 'connection', 'delete', 'uuid', identifier])
        return {'message': 'Saved Wi-Fi network removed'}
    if action == 'nano-install':
        return nano_install(request)
    if action in ('nano-prepare', 'nano-eject'):
        device = ipod_device()
        if action == 'nano-prepare':
            run(['/usr/bin/sg_start', device, '--loej'], check=False)
            run(['/usr/bin/umount', device + '1'], check=False)
        else:
            run(['/usr/bin/eject', device])
        return {'message': 'Nano ready'}
    raise ValueError('Unsupported Pi action')


def main():
    if os.geteuid() != 0:
        raise SystemExit('This helper must run through its installed sudo rule')
    RUNTIME.mkdir(exist_ok=True, mode=0o700)
    try:
        raw = sys.stdin.buffer.read(48 * 1024 * 1024 + 1)
        if len(raw) > 48 * 1024 * 1024:
            raise ValueError('Request is too large')
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError('Expected a JSON object')
        result = dispatch(request)
        print(json.dumps(result))
    except Exception:
        # Never echo requests, passwords, subprocess output or credentials.
        print(json.dumps({'error': 'Pi action failed. Check the selected network, USB connection and helper installation.'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
