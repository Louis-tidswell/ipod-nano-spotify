import io
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from spotify_content import SpotifyContent, image_rgb565, ART_BYTES
from spotify_bridge import Snapshot, encode_status, page_checksum

URI = 'spotify:playlist:0123456789ABCDEFGHIJKL'


class ContentTests(unittest.TestCase):
    def test_cover_pixels_are_little_endian_rgb565(self):
        image = Image.new('RGB', (100, 50), (255, 0, 0))
        raw = io.BytesIO(); image.save(raw, format='PNG')
        pixels = image_rgb565(raw.getvalue())
        self.assertEqual(len(pixels), ART_BYTES)
        self.assertEqual(pixels, b'\x00\xf8' * (ART_BYTES // 2))

    def test_all_art_chunks_fit_status_and_reassemble(self):
        pixels = bytes(range(256)) * (ART_BYTES // 256)
        assembled = bytearray()
        for i in range(ART_BYTES // 256):
            page = encode_status(i+1, 2, 3, Snapshot(track='Song'), art=(123, pixels, i))
            self.assertEqual(len(page),512)
            self.assertEqual(struct.unpack_from('<5I',page,236), (3,123,ART_BYTES,i,256))
            self.assertEqual(struct.unpack_from('<I',page,40)[0], page_checksum(page,10))
            assembled += page[256:]
        self.assertEqual(assembled, pixels)

    def test_library_uri_and_unicode_name_fit_packet(self):
        items = [dict(uri=URI, name='é'*100, length=50)]*2
        page = encode_status(1,2,3,Snapshot(), library=(2,67,3,'Choose a playlist',items))
        self.assertEqual(struct.unpack_from('<5I',page,236),(2,2,67,3,2))
        for row in range(2):
            uri,name,length = struct.unpack_from('<40s52sI',page,320+row*96)
            self.assertEqual(uri.rstrip(b'\0').decode(),URI)
            name.rstrip(b'\0').decode('utf-8')
            self.assertEqual(length,50)

    def test_play_uses_full_uri_not_reordered_list_index(self):
        api=Mock(); content=SpotifyContent(api)
        content.items=[dict(uri=URI,name='Playlist',length=1)]
        content.start()
        try:
            content.submit(21,0,URI)
            for _ in range(100):
                if content.snapshot()[0]==2: break
                time.sleep(.01)
            api.request.assert_called_once_with('/player/play',{'uri':URI},timeout=25)
        finally: content.close()

    def test_account_change_discards_old_playlist_response(self):
        started=threading.Event();release=threading.Event()
        def request(*args,**kwargs):
            started.set();release.wait(2)
            return dict(total=1,items=[dict(uri=URI,name='Old account',length=1)])
        content=SpotifyContent(Mock(request=request));content.observe('old','');content.start()
        try:
            content.submit(20,0);self.assertTrue(started.wait(1))
            content.observe('new','');release.set();time.sleep(.1)
            self.assertEqual(content.snapshot()[4],[])
        finally:release.set();content.close()

    def test_stale_or_invalid_uri_is_rejected(self):
        content=SpotifyContent(Mock())
        content.submit(21,0,'spotify:playlist:invalid')
        self.assertEqual(content.snapshot()[0],4)
        self.assertTrue(content.jobs.empty())

if __name__ == '__main__': unittest.main()
