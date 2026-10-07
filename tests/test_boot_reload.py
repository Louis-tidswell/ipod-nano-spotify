"""Boot-only reload behaviour without touching USB or restarting live services."""
import configparser
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pi'))
from reload_nano_at_boot import run, USB_WAIT, STARTUP_WINDOW


class BootReloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        self.state, self.boot, self.uptime = (directory / name for name in ('state/attempt', 'boot-id', 'uptime'))
        self.boot.write_text('boot-one')
        self.uptime.write_text('10.0 100.0')
        self.find = Mock(return_value='/dev/sg0')
        self.execute = Mock(side_effect=[Mock(returncode=3), Mock(returncode=0)])
        self.elapsed = 0.0

    def tearDown(self):
        self.temp.cleanup()

    def sleep(self, duration):
        self.elapsed += duration

    def reload(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(self.state, self.boot, self.uptime, self.find, self.execute,
                       lambda: self.elapsed, self.sleep)

    def test_connected_nano_reloads_existing_apps_only(self):
        self.assertEqual(self.reload(), 0)
        command = self.execute.call_args_list[1].args[0]
        self.assertEqual(command[-2:], ['--managed', '--reload-only'])
        self.assertEqual(Path(command[2]), ROOT / 'pi/install_nanoapps.py')
        self.assertEqual(self.execute.call_args_list[1].kwargs['timeout'], 60)
        self.assertEqual(self.state.read_text().strip(), 'boot-one')
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_absent_nano_skips_and_later_usb_connection_does_not_reload(self):
        self.find.side_effect = RuntimeError('No iPod')
        self.assertEqual(self.reload(), 0)
        self.assertEqual(self.elapsed, USB_WAIT)
        self.execute.assert_not_called()
        self.find.side_effect = None
        self.find.reset_mock()
        self.assertEqual(self.reload(), 0)
        self.find.assert_not_called()
        self.execute.assert_not_called()

    def test_usb_enumeration_can_settle_during_startup(self):
        self.find.side_effect = [RuntimeError('Not yet'), RuntimeError('Not yet'), '/dev/sg0']
        self.assertEqual(self.reload(), 0)
        self.assertEqual(self.elapsed, 1)
        self.assertEqual(self.execute.call_count, 2)

    def test_same_boot_service_restart_skips_but_new_pi_boot_reloads(self):
        self.assertEqual(self.reload(), 0)
        self.execute.reset_mock(side_effect=True)
        self.find.reset_mock()
        self.assertEqual(self.reload(), 0)
        self.find.assert_not_called()
        self.execute.assert_not_called()
        self.boot.write_text('boot-two')
        self.execute.side_effect = [Mock(returncode=3), Mock(returncode=0)]
        self.assertEqual(self.reload(), 0)
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual(self.state.read_text().strip(), 'boot-two')

    def test_installing_or_starting_unit_later_in_the_day_never_touches_usb(self):
        self.uptime.write_text(str(STARTUP_WINDOW + 1) + ' 1000')
        self.assertEqual(self.reload(), 0)
        self.find.assert_not_called()
        self.execute.assert_not_called()

    def test_usb_wait_does_not_extend_beyond_the_startup_window(self):
        self.uptime.write_text(str(STARTUP_WINDOW - 2) + ' 1000')
        self.find.side_effect = RuntimeError('No iPod')
        self.assertEqual(self.reload(), 0)
        self.assertEqual(self.elapsed, 2)
        self.execute.assert_not_called()

    def test_failed_reload_is_not_retried_on_the_same_boot(self):
        self.execute.side_effect = [Mock(returncode=3), Mock(returncode=7)]
        self.assertEqual(self.reload(), 7)
        self.execute.reset_mock(side_effect=True)
        self.assertEqual(self.reload(), 0)
        self.execute.assert_not_called()

    def test_active_bridge_skips_to_avoid_usb_conflicts_and_ordering_deadlock(self):
        self.execute.side_effect = [Mock(returncode=0)]
        self.assertEqual(self.reload(), 0)
        self.assertEqual(self.execute.call_count, 1)

    def test_unit_runs_once_and_orders_usb_work_before_live_services(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / 'pi/ipod-nano-boot-reload.service')
        self.assertEqual(unit['Service']['Type'], 'oneshot')
        self.assertTrue(unit['Service'].getboolean('RemainAfterExit'))
        self.assertNotIn('Restart', unit['Service'])
        self.assertEqual(set(unit['Unit']['Before'].split()),
                         {'ipod-spotify-bridge.service', 'ipod-nano-config.service'})
        self.assertNotIn('Requires', unit['Unit'])
        self.assertEqual(unit['Install']['WantedBy'], 'default.target')
        self.assertGreater(int(unit['Service']['TimeoutStartSec']), USB_WAIT + 60)


if __name__ == '__main__':
    unittest.main()
