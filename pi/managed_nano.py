"""Use upstream NanoApps builds with the restricted web installation helper."""
import base64
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

HELPER = '/usr/local/libexec/ipod-nano-config-helper'


def helper(action, **values):
    result = subprocess.run(['sudo', '-n', HELPER], input=json.dumps(dict(action=action, **values)),
                            text=True, capture_output=True, timeout=180)
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError('Pi controls need setup: run pi/setup_config.sh once on the Pi') from None
    if result.returncode:
        raise RuntimeError(response.get('error', 'Pi action failed'))
    return response


def install(nanoapps: Path, targets, reload_only=False):
    loader = importlib.machinery.SourceFileLoader('nanoapps_start', str(nanoapps / 'start'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    upstream = importlib.util.module_from_spec(spec)
    loader.exec_module(upstream)
    from spotify_bridge import address_bytes, find_ipod

    class Transport(upstream.Transport):
        def __init__(self):
            super().__init__('local', None)

        def wait_connected(self):
            helper('nano-prepare')
            return True

        def sh(self, command, capture=False, check=True):
            if isinstance(command, str) and command.startswith('sudo umount '):
                helper('nano-prepare')
                return subprocess.CompletedProcess([], 0)
            return super().sh(command, capture, check)

        def eject(self):
            helper('nano-eject')

        def scsi_write_file(self, local_bin, address):
            if address != 0x09130000:
                raise RuntimeError('Unexpected resident link address; refusing to upload')
            data = Path(local_bin).read_bytes()
            device = find_ipod()
            with tempfile.NamedTemporaryFile() as chunk:
                for offset in range(0, len(data), 512):
                    chunk.seek(0)
                    chunk.write(data[offset:offset + 512].ljust(512, b'\0'))
                    chunk.truncate(512)
                    chunk.flush()
                    subprocess.run(['sg_raw', '-s', '512', '-i', chunk.name, device, 'c6', '96', '01',
                                    *address_bytes(address + offset)], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            print(f'Uploaded resident ({len(data)} bytes)', flush=True)

        def scsi_exec(self, address):
            if address != 0x09130001:
                raise RuntimeError('Unexpected resident entry address')
            subprocess.run(['sg_raw', '-o', '/dev/null', '-r', '512', find_ipod(), 'c6', '96', '03',
                            *address_bytes(address)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)

    def install_disk(_transport, source, _data, _force):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
            for file in sorted((Path(source) / 'Apps').rglob('*')):
                if file.is_file():
                    archive.write(file, file.relative_to(source))
        print(helper('nano-install', archive=base64.b64encode(output.getvalue()).decode())['message'], flush=True)

    upstream._install_disk = install_disk
    transport = Transport()
    # Always verify that USB is present before spending time compiling.
    find_ipod()
    if reload_only:
        upstream._push_run(transport, 'silver_resident')
        print('Apps reloaded. Open Music or Settings, then return Home.', flush=True)
    else:
        upstream._deploy_apps(transport, targets, False, False, False)
