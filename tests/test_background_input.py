"""Background input boundaries, without sending any real desktop input."""
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wuwa_helper import windows_api as api


class BackgroundInputTests(unittest.TestCase):
    def setUp(self):
        self.target = api.Target(10, 77, (-2560, 0, 0, 1440))
        self.hit, self.root, self.thread, self.pid = 21, 20, 88, 77
        self.running = True
        self.client = (110, 80)
        self.rect = self.target.rect
        self.screen_points = []
        forbidden = lambda: Mock(side_effect=AssertionError('foreground input used'))
        self.user32 = SimpleNamespace(
            IsWindowVisible=Mock(return_value=True), IsIconic=Mock(return_value=False),
            WindowFromPoint=Mock(side_effect=lambda point: self.hit),
            GetAncestor=Mock(side_effect=lambda hwnd, flag: self.root),
            GetWindowRect=Mock(side_effect=self.window_rect),
            GetWindowThreadProcessId=Mock(side_effect=self.window_thread),
            ScreenToClient=Mock(side_effect=self.to_client),
            PostMessageW=Mock(return_value=True),
            SetCursorPos=forbidden(), GetCursorPos=forbidden(),
            SendInput=forbidden(), GetForegroundWindow=forbidden(),
            SetForegroundWindow=forbidden(), GetAsyncKeyState=forbidden(),
        )
        self.patches = [
            patch.object(api, 'u32', self.user32),
            patch.object(api, 'title', side_effect=lambda hwnd:
                         'Wuwa Tappo' if hwnd == 10 else 'DesktopCatInput'),
            patch.object(api, 'identity', side_effect=lambda hwnd: (self.pid, 'wuwatappo.exe')),
            patch.object(api, 'ensure_input_access'),
            patch.object(api.time, 'sleep'),
            patch.object(api, 'held_keys', forbidden()),
            patch.object(api, 'idle_seconds', forbidden()),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def window_rect(self, hwnd, pointer):
        rect = C.cast(pointer, C.POINTER(W.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = self.rect
        return True

    def window_thread(self, hwnd, pointer):
        C.cast(pointer, C.POINTER(W.DWORD)).contents.value = self.pid
        return self.thread

    def to_client(self, hwnd, pointer):
        point = C.cast(pointer, C.POINTER(W.POINT)).contents
        self.screen_points.append((hwnd, point.x, point.y))
        point.x, point.y = self.client
        return True

    def click(self):
        return api.click_background(self.target, -1800, 700, lambda: self.running)

    def messages(self):
        return [call.args for call in self.user32.PostMessageW.call_args_list]

    def test_exact_child_receives_complete_click_without_cursor_focus_or_idle(self):
        self.assertTrue(self.click())
        point = 110 | (80 << 16)
        self.assertEqual(self.messages(), [
            (21, 0x0200, 0, point), (21, 0x0201, 1, point), (21, 0x0202, 0, point),
        ])
        self.assertEqual(self.screen_points, [(21, -1800, 700)])
        api.ensure_input_access.assert_called_once_with(self.target)

    def test_main_window_can_be_the_recipient(self):
        self.hit = self.root = self.target.hwnd
        self.assertTrue(self.click())
        self.assertTrue(all(message[0] == self.target.hwnd for message in self.messages()))

    def test_signed_client_coordinates_are_packed_without_screen_coordinate_leak(self):
        self.client = (-3, -4)
        self.assertTrue(self.click())
        self.assertEqual(self.messages()[1][3], 0xfffcfffd)

    def test_cancelled_before_input_does_not_even_resolve_a_window(self):
        self.running = False
        self.assertFalse(self.click())
        api.ensure_input_access.assert_not_called()
        self.user32.WindowFromPoint.assert_not_called()
        self.user32.PostMessageW.assert_not_called()

    def test_input_permission_error_does_not_fall_back(self):
        api.ensure_input_access.side_effect = RuntimeError('permission denied')
        with self.assertRaisesRegex(RuntimeError, 'permission denied'):
            self.click()
        self.user32.PostMessageW.assert_not_called()

    def test_obscured_or_moved_target_does_not_send_messages(self):
        self.rect = (-2559, 0, 1, 1440)
        self.assertFalse(self.click())
        self.user32.PostMessageW.assert_not_called()

    def test_child_from_a_different_process_is_rejected(self):
        api.identity.side_effect = lambda hwnd: (99 if hwnd == self.hit else 77, 'wuwatappo.exe')
        self.assertFalse(self.click())
        self.user32.PostMessageW.assert_not_called()

    def test_foreign_root_is_rejected(self):
        api.title.side_effect = lambda hwnd: 'Wuwa Tappo' if hwnd == 10 else 'Other app'
        self.assertFalse(self.click())
        self.user32.PostMessageW.assert_not_called()

    def test_coordinate_conversion_failure_sends_nothing(self):
        self.user32.ScreenToClient.side_effect = None
        self.user32.ScreenToClient.return_value = False
        with self.assertRaisesRegex(RuntimeError, '換算'):
            self.click()
        self.user32.PostMessageW.assert_not_called()

    def test_unrepresentable_client_coordinates_are_rejected(self):
        self.client = (32768, 0)
        with self.assertRaisesRegex(RuntimeError, '範圍'):
            self.click()
        self.user32.PostMessageW.assert_not_called()

    def test_cancellation_after_hover_never_posts_button_down(self):
        def post(hwnd, message, buttons, point):
            self.running = False
            return True
        self.user32.PostMessageW.side_effect = post
        self.assertFalse(self.click())
        self.assertEqual([item[1] for item in self.messages()], [0x0200])

    def test_geometry_is_checked_again_after_hover(self):
        def post(hwnd, message, buttons, point):
            self.rect = (-2559, 0, 1, 1440)
            return True
        self.user32.PostMessageW.side_effect = post
        self.assertFalse(self.click())
        self.assertEqual([item[1] for item in self.messages()], [0x0200])

    def test_cancellation_after_down_reports_attempt_and_releases_original_recipient(self):
        def post(hwnd, message, buttons, point):
            if message == 0x0201:
                self.running = False
                self.hit = 999  # An unrelated window now covers the point.
            return True
        self.user32.PostMessageW.side_effect = post
        self.assertTrue(self.click())
        self.assertEqual([item[:3] for item in self.messages()], [
            (21, 0x0200, 0), (21, 0x0201, 1), (21, 0x0202, 0),
        ])

    def test_exception_after_down_still_releases(self):
        api.time.sleep.side_effect = ValueError('interrupted')
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            self.click()
        self.assertEqual(self.messages()[-1][:3], (21, 0x0202, 0))

    def test_post_failure_is_reported_and_never_uses_global_input(self):
        self.user32.PostMessageW.side_effect = [True, False]
        with patch.object(api.C, 'get_last_error', return_value=5):
            with self.assertRaisesRegex(RuntimeError, '錯誤 5'):
                self.click()
        self.assertEqual([item[1] for item in self.messages()], [0x0200, 0x0201])

    def test_release_failure_is_reported(self):
        self.user32.PostMessageW.side_effect = [True, True, False]
        with self.assertRaisesRegex(RuntimeError, '背景滑鼠訊息'):
            self.click()

    def test_recycled_foreign_handle_does_not_receive_release(self):
        def post(hwnd, message, buttons, point):
            if message == 0x0201:
                self.pid = 99
            return True
        self.user32.PostMessageW.side_effect = post
        with self.assertRaisesRegex(RuntimeError, '輸入視窗已變更'):
            self.click()
        self.assertEqual([item[1] for item in self.messages()], [0x0200, 0x0201])

    def test_replaced_input_thread_does_not_receive_release(self):
        def post(hwnd, message, buttons, point):
            if message == 0x0201:
                self.thread = 89
            return True
        self.user32.PostMessageW.side_effect = post
        with self.assertRaisesRegex(RuntimeError, '輸入視窗已變更'):
            self.click()
        self.assertEqual([item[1] for item in self.messages()], [0x0200, 0x0201])


if __name__ == '__main__':
    unittest.main()
