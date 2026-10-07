"""Private Unix socket lets the LAN page use the bridge's Bluetooth worker."""
import json
import os
import re
import socketserver
import threading
from pathlib import Path


class BridgeControl:
    def __init__(self, bluetooth):
        self.bluetooth = bluetooth
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid())))
        self.path = runtime / 'ipod-spotify-control.sock'
        self.path.unlink(missing_ok=True)
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(2)
                try:
                    request = json.loads(self.rfile.readline(4097))
                    if request.get('action') == 'command':
                        command, address = request.get('command'), request.get('address', '')
                        if command not in (11, 12, 13, 14) or not isinstance(address, str) or (command != 11 and not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', address)):
                            raise ValueError('Choose a valid Bluetooth action and device')
                        owner.bluetooth.submit(command, 0, address)
                    elif request.get('action') != 'status':
                        raise ValueError('Unknown bridge action')
                    with owner.bluetooth.lock:
                        result = dict(flags=owner.bluetooth.flags, message=owner.bluetooth.message,
                                      devices=[{k: d[k] for k in ('address', 'name', 'flags')} for d in owner.bluetooth.devices])
                except Exception:
                    result = {'error': 'Bluetooth action failed; wait for the current action or check the bridge'}
                self.wfile.write(json.dumps(result).encode() + b'\n')

        self.server = socketserver.ThreadingUnixStreamServer(str(self.path), Handler)
        self.server.daemon_threads = True
        self.path.chmod(0o600)

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.path.unlink(missing_ok=True)
