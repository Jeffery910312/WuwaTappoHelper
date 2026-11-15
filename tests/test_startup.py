"""Exercise UAC startup decisions without opening a prompt or sending input."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wuwa_helper import startup
from wuwa_helper.paths import PROJECT_ROOT


class AdminStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parse = C.WinDLL('shell32').CommandLineToArgvW
        cls.parse.argtypes = [W.LPCWSTR, C.POINTER(C.c_int)]
        cls.parse.restype = C.POINTER(W.LPWSTR)
        cls.free = C.WinDLL('kernel32').LocalFree
        cls.free.argtypes = [W.HLOCAL]
        cls.free.restype = W.HLOCAL

    def setUp(self):
        self.shell = SimpleNamespace(
            IsUserAnAdmin=Mock(return_value=False),
            ShellExecuteW=Mock(return_value=33),
        )
        self.patch = patch.object(startup.C, 'WinDLL', return_value=self.shell)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_already_admin_does_not_relaunch(self):
        self.shell.IsUserAnAdmin.return_value = True
        self.assertTrue(startup.ensure_admin(relaunched=True))
        self.shell.ShellExecuteW.assert_not_called()

    def test_uac_launch_preserves_paths_arguments_and_interpreter(self):
        directory = Path('D:/測試 空白 & (資料夾)')
        arguments = ['with spaces', 'a"b', 'trailing slash \\', '中文', '']
        executable = 'C:\\Python with spaces\\pythonw.exe'
        with patch.object(startup, 'PROJECT_ROOT', directory), \
                patch.object(sys, 'argv', ['main.py', *arguments]), \
                patch.object(sys, 'executable', executable):
            self.assertFalse(startup.ensure_admin())
        hwnd, verb, program, parameters, cwd, show = self.shell.ShellExecuteW.call_args.args
        self.assertEqual((hwnd, verb, program, cwd, show),
                         (None, 'runas', executable, str(directory), 1))
        count = C.c_int()
        parsed = self.parse('pythonw.exe ' + parameters, C.byref(count))
        self.assertTrue(parsed)
        try:
            self.assertEqual(list(parsed[:count.value]), [
                'pythonw.exe', str(directory / 'Launch.pyw'),
                startup.ELEVATED_FLAG, *arguments,
            ])
        finally:
            self.free(C.cast(parsed, W.HLOCAL))

    def test_cancelled_uac_stops_startup(self):
        self.shell.ShellExecuteW.return_value = 5
        with patch.object(startup.C, 'get_last_error', return_value=1223):
            with self.assertRaisesRegex(PermissionError, '已取消管理員授權'):
                startup.ensure_admin()

    def test_failed_uac_stops_startup(self):
        self.shell.ShellExecuteW.return_value = 2
        with patch.object(startup.C, 'get_last_error', return_value=2):
            with self.assertRaises(OSError) as error:
                startup.ensure_admin()
        self.assertEqual(error.exception.winerror, 2)

    def test_null_shell_result_is_failure(self):
        self.shell.ShellExecuteW.return_value = None
        with patch.object(startup.C, 'get_last_error', return_value=5):
            with self.assertRaises(OSError):
                startup.ensure_admin()

    def test_failed_elevation_cannot_repeat_uac(self):
        with self.assertRaisesRegex(PermissionError, '未取得管理員權限'):
            startup.ensure_admin(relaunched=True)
        self.shell.ShellExecuteW.assert_not_called()


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.launch = runpy.run_path(str(PROJECT_ROOT / 'Launch.pyw'))['launch']
        self.app = Mock()
        self.patch = patch.dict(sys.modules, {'wuwa_helper.main': SimpleNamespace(main=self.app)})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_parent_does_not_start_gui_after_handoff(self):
        with patch.object(startup, 'ensure_admin', return_value=False):
            self.launch()
        self.app.assert_not_called()

    def test_elevated_child_consumes_internal_flag_before_app(self):
        arguments = ['Launch.pyw', startup.ELEVATED_FLAG, '--example', '中文 空白']
        with patch.object(sys, 'argv', arguments), \
                patch.object(startup, 'ensure_admin', return_value=True) as admin:
            self.launch()
            admin.assert_called_once_with(relaunched=True)
            self.assertEqual(sys.argv, ['Launch.pyw', '--example', '中文 空白'])
        self.app.assert_called_once_with()

    def test_failure_is_logged_and_shown_without_starting_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'Launch.pyw'
            with patch.dict(self.launch.__globals__, {'__file__': str(entry)}), \
                    patch.object(startup, 'ensure_admin', side_effect=PermissionError('授權取消')), \
                    patch.object(C.windll.user32, 'MessageBoxW') as message:
                with self.assertRaises(SystemExit) as error:
                    self.launch()
                self.assertEqual(error.exception.code, 1)
                self.assertIn('授權取消', message.call_args.args[1])
            self.assertIn('授權取消', (Path(directory) / 'startup.log').read_text(encoding='utf-8'))
        self.app.assert_not_called()


if __name__ == '__main__':
    unittest.main()
