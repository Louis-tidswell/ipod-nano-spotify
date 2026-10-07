"""Build the real Nano UI with native LVGL, verify bounds and save screenshots."""
from pathlib import Path
import concurrent.futures
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pi'))
from spotify_bridge import Snapshot, encode_status
from spotify_content import ART_BYTES

out = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/nano-ui-native')
out.mkdir(parents=True, exist_ok=True)
upstream = ROOT / 'vendor/NanoApps'
flags = ['-O1', '-DHB_LV_RELOC', '-DLV_CONF_INCLUDE_SIMPLE', '-DLV_CONF_PATH="' + str(upstream / 'sdk/lv_conf.h') + '"',
         '-I' + str(ROOT / 'tests/native'), '-I' + str(upstream / 'sdk'), '-I' + str(upstream / 'lvgl'), '-I' + str(upstream / 'lvgl/include'), '-I' + str(upstream / 'lvgl/src')]
archive = out / 'liblvgl.a'
if not archive.exists():
    result = subprocess.check_output(['make', '-s', '-C', str(upstream / 'apps/spotify_remote'),
                                     '--eval', 'native-sources:;@echo $(LVGL_SRCS)', 'native-sources'], text=True)
    objects = out / 'objects'; objects.mkdir(exist_ok=True)
    def compile(item):
        index, source = item
        target = objects / f'{index}.o'
        subprocess.run(['cc', *flags, '-w', '-c', source, '-o', str(target)], check=True, capture_output=True)
        return str(target)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        built = list(pool.map(compile, enumerate(result.split())))
    subprocess.run(['ar', 'rcs', str(archive), *built], check=True)
status = Snapshot(flags=3, track='A song with a very long track title for testing', artist='The Artist', duration_ms=180000, position_ms=60000, volume_percent=45, message='Playing')
bt = (17, 7, 0, 'Choose a speaker or Scan', [dict(address='C0:28:8D:72:38:C2', name='WONDERBOOM', flags=7),
     dict(address='AA:BB:CC:DD:EE:01', name='QuietComfort Headphones', flags=1), dict(address='AA:BB:CC:DD:EE:02', name='A new speaker with a long device name', flags=0)])
(out / 'bt.bin').write_bytes(encode_status(1, 1, 1, status, bt))
(out / 'config.bin').write_bytes(encode_status(1, 1, 1, status, config='http://192.168.1.120:8080'))
library = (2, 67, 0, 'Choose a playlist', [dict(uri='spotify:playlist:ABCDEFGHIJKL0123456789', name='Psych-rock Bangers', length=43),
           dict(uri='spotify:playlist:0123456789ABCDEFGHIJKL', name='Playlist with a long name that wraps across lines', length=50)])
(out / 'library.bin').write_bytes(encode_status(1, 1, 1, status, library=library))
pixels = bytes([0xe0, 0x07]) * (ART_BYTES // 2)
for i in range(ART_BYTES // 256):
    (out / f'art-{i}.bin').write_bytes(encode_status(i + 1, 1, 1, status, art=(1234, pixels, i)))
subprocess.run(['cc', *flags, '-DNANO_UI_TEST', str(ROOT / 'tests/native/nano_ui.c'), str(archive), '-lm', '-o', str(out / 'nano-ui-test')], check=True)
subprocess.run([str(out / 'nano-ui-test'), str(out)], check=True)
from PIL import Image
for file in out.glob('*.ppm'):
    Image.open(file).save(file.with_suffix('.png'))
print('Screen previews:', out)
