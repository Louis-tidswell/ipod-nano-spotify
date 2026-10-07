import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from bluetooth_control import BluetoothControl
from spotify_bridge import (COMMAND_HEADER, NANO_TO_PI_MAGIC, PROTOCOL_VERSION,
                            Snapshot, decode_command, encode_status, page_checksum)


class ProtocolTests(unittest.TestCase):
    def test_address_corruption_rejected_and_signed_volume_preserved(self):
        page = bytearray(512)
        COMMAND_HEADER.pack_into(page, 0, NANO_TO_PI_MAGIC, PROTOCOL_VERSION, 7, 12, 0xfffffffb, 123, 0)
        page[28:46] = b'C0:28:8D:72:38:C2\0'
        struct.pack_into('<I', page, 24, page_checksum(page, 6))
        self.assertEqual(decode_command(page), (7, 12, -5, 123, 'C0:28:8D:72:38:C2'))
        page[30] ^= 1
        self.assertIsNone(decode_command(page))
        self.assertIsNone(decode_command(b'short'))

    def test_three_devices_fit_page_and_names_are_bounded(self):
        devices = [dict(address='C0:28:8D:72:38:C2', flags=7, name='é' * 100)] * 3
        page = encode_status(1, 2, 3, Snapshot(track='Song'), (17, 8, 2, 'Ready', devices))
        self.assertEqual(len(page), 512)
        self.assertEqual(struct.unpack_from('<5I', page, 236), (1, 17, 8, 2, 3))
        self.assertEqual(struct.unpack_from('<I', page, 40)[0], page_checksum(page, 10))
        for row in range(3):
            address, flags, name = struct.unpack_from('<18sH44s', page, 320 + row * 64)
            self.assertEqual(address.rstrip(b'\0'), b'C0:28:8D:72:38:C2')
            self.assertEqual(flags, 7)
            self.assertEqual(name[-1], 0)
            name.rstrip(b'\0').decode('utf-8')


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bt = BluetoothControl(Path(self.temp.name) / 'prefs.json')
        self.bt.preferences = dict(adapter='USB', preferred='C0:28:8D:72:38:C2', reconnect=True)
        self.device = dict(address='C0:28:8D:72:38:C2', name='Speaker', path='/usb/speaker', flags=7)
        self.bt.devices = [self.device]
        self.bt.adapter = '/usb'

    @patch('bluetooth_control.call')
    def test_disconnect_persists_opt_out_before_call(self, call):
        with patch.object(self.bt, 'refresh'):
            self.bt.action(13, self.device['address'])
        self.assertFalse(BluetoothControl(self.bt.config).preferences['reconnect'])
        call.assert_called_once_with('/usb/speaker', 'org.bluez.Device1', 'Disconnect')

    @patch('bluetooth_control.call')
    def test_stale_device_never_controls_another_speaker(self, call):
        with patch.object(self.bt, 'refresh'):
            with self.assertRaisesRegex(RuntimeError, 'Device gone'):
                self.bt.action(12, 'AA:BB:CC:DD:EE:FF')
        call.assert_not_called()

    @patch('bluetooth_control.audio_nodes', return_value=[])
    @patch('bluetooth_control.objects')
    def test_saved_devices_are_scoped_to_pinned_adapter(self, objects, nodes):
        objects.return_value = {
            '/usb': {'org.bluez.Adapter1': {'Address': 'USB', 'Powered': True}},
            '/internal': {'org.bluez.Adapter1': {'Address': 'INTERNAL', 'Powered': True}},
            '/usb/speaker': {'org.bluez.Device1': {'Adapter': '/usb', 'Address': self.device['address'],
                            'Alias': 'Speaker', 'Paired': True, 'Bonded': True, 'Icon': 'audio-headset'}},
            '/internal/headphones': {'org.bluez.Device1': {'Adapter': '/internal', 'Address': 'AA:BB:CC:DD:EE:FF',
                            'Alias': 'Wrong adapter', 'Paired': True, 'Bonded': True, 'Icon': 'audio-headset'}}}
        self.bt.refresh()
        self.assertEqual([d['name'] for d in self.bt.devices], ['Speaker'])

    @patch('bluetooth_control.audio_nodes', return_value=[])
    @patch('bluetooth_control.objects')
    def test_discovered_audio_class_is_visible_before_uuids_arrive(self, objects, nodes):
        objects.return_value = {
            '/usb': {'org.bluez.Adapter1': {'Address': 'USB', 'Powered': True}},
            '/usb/speaker': {'org.bluez.Device1': {'Adapter': '/usb', 'Address': self.device['address'],
                            'Alias': 'WONDERBOOM', 'Class': 0x240404, 'Paired': False}}}
        self.bt.refresh()
        self.assertEqual([d['name'] for d in self.bt.devices], ['WONDERBOOM'])

    def test_busy_rejects_second_mutating_action(self):
        self.bt.submit(12, 0, self.device['address'])
        self.bt.submit(13, 0, self.device['address'])
        self.assertEqual(self.bt.jobs.qsize(), 1)
        self.assertEqual(self.bt.jobs.get()[0], 12)


if __name__ == '__main__':
    unittest.main()
