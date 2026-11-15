"""Native frame styling boundaries; no real windows or desktop changes."""
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from wuwa_helper import window_style as appearance


class WindowStyleTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.hwnd = 0x123456789  # Exercise a handle larger than 32 bits.

    def record(self, hwnd, attribute, pointer, size):
        value = C.cast(pointer, C.POINTER(C.c_uint32)).contents.value
        self.calls.append((hwnd, attribute, value, size))
        return 0

    def apply(self, **changes):
        settings = dict(dark=True, caption_color='#123456',
                        border_color='#ABCDEF', text_color='#F0E1D2')
        settings.update(changes)
        return appearance.apply_window_style(self.hwnd, **settings)

    def test_colors_and_large_handle_reach_only_requested_native_window(self):
        with patch.object(appearance, '_load_dwm', return_value=self.record):
            self.assertTrue(self.apply())
        self.assertEqual(self.calls, [
            (self.hwnd, 20, 1, 4),
            (self.hwnd, 33, 2, 4),
            (self.hwnd, 34, 0xEFCDAB, 4),
            (self.hwnd, 35, 0x563412, 4),
            (self.hwnd, 36, 0xD2E1F0, 4),
        ])

    def test_switching_to_light_clears_native_dark_flag(self):
        with patch.object(appearance, '_load_dwm', return_value=self.record):
            self.assertTrue(self.apply(dark=False))
        self.assertEqual(self.calls[0], (self.hwnd, 20, 0, 4))

    def test_unsupported_attribute_does_not_prevent_other_supported_colors(self):
        def partial(hwnd, attribute, pointer, size):
            self.record(hwnd, attribute, pointer, size)
            return 0x80070057 if attribute == 33 else 0  # E_INVALIDARG.

        with patch.object(appearance, '_load_dwm', return_value=partial):
            self.assertFalse(self.apply())
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.calls[-1][1], 36)

    def test_missing_dwm_and_zero_handle_fail_gracefully(self):
        with patch.object(appearance, '_load_dwm', return_value=None):
            self.assertFalse(self.apply())
        with patch.object(appearance, '_load_dwm') as loader:
            self.assertFalse(appearance.apply_window_style(
                0, dark=True, caption_color='#000000',
                border_color='#000000', text_color='#FFFFFF'))
        loader.assert_not_called()

    def test_invalid_color_never_partially_modifies_a_window(self):
        setter = Mock()
        with patch.object(appearance, '_load_dwm', return_value=setter):
            for invalid in ('red', '#123', '#ZZFFFF', None):
                with self.subTest(color=invalid):
                    with self.assertRaises(ValueError):
                        self.apply(border_color=invalid)
        setter.assert_not_called()

    def test_native_error_does_not_escape_to_gui(self):
        with patch.object(appearance, '_load_dwm', return_value=Mock(side_effect=OSError('unsupported'))):
            self.assertFalse(self.apply())

    def test_tk_client_handle_resolves_to_native_root_without_truncation(self):
        frame = 0xABCDEF012
        get_ancestor = Mock(return_value=frame)
        with patch.object(appearance, '_load_get_ancestor', return_value=get_ancestor):
            self.assertEqual(appearance.get_frame_handle(self.hwnd), frame)
        get_ancestor.assert_called_once_with(self.hwnd, 2)

    def test_missing_or_invalid_native_frame_returns_zero(self):
        with patch.object(appearance, '_load_get_ancestor', return_value=None):
            self.assertEqual(appearance.get_frame_handle(self.hwnd), 0)
        with patch.object(appearance, '_load_get_ancestor', return_value=Mock(return_value=0)):
            self.assertEqual(appearance.get_frame_handle(self.hwnd), 0)
        with patch.object(appearance, '_load_get_ancestor', return_value=Mock(side_effect=OSError('gone'))):
            self.assertEqual(appearance.get_frame_handle(self.hwnd), 0)

    def test_dwm_binding_uses_pointer_safe_hwnd_and_signed_hresult(self):
        appearance._load_dwm.cache_clear()
        function = Mock()
        try:
            with patch.object(appearance.C, 'WinDLL', return_value=SimpleNamespace(DwmSetWindowAttribute=function)):
                self.assertIs(appearance._load_dwm(), function)
            self.assertEqual(function.argtypes, [W.HWND, W.DWORD, C.c_void_p, W.DWORD])
            self.assertIs(function.restype, C.c_int32)
        finally:
            appearance._load_dwm.cache_clear()

    def frame_api(self, style=0x00040100):
        return SimpleNamespace(get_style=Mock(return_value=style),
                               set_style=Mock(return_value=style),
                               clear_icon=Mock(return_value=0),
                               refresh=Mock(return_value=True))

    def test_hiding_caption_icon_preserves_all_other_window_styles_and_geometry(self):
        api = self.frame_api()
        with patch.object(appearance, '_load_frame_api', return_value=api):
            self.assertTrue(appearance.hide_caption_icon(self.hwnd))
        api.get_style.assert_called_once_with(self.hwnd, -20)
        api.set_style.assert_called_once_with(self.hwnd, -20, 0x00040101)
        api.clear_icon.assert_has_calls([call(self.hwnd, 0x80, 0, 0),
                                         call(self.hwnd, 0x80, 1, 0)])
        # Refresh only the frame: no resize, movement, activation or Z-order change.
        api.refresh.assert_called_once_with(self.hwnd, None, 0, 0, 0, 0, 0x0037)

    def test_hiding_caption_icon_is_idempotent_for_an_already_styled_frame(self):
        api = self.frame_api(style=0x00040101)
        with patch.object(appearance, '_load_frame_api', return_value=api):
            self.assertTrue(appearance.hide_caption_icon(self.hwnd))
        api.set_style.assert_not_called()
        api.clear_icon.assert_has_calls([call(self.hwnd, 0x80, 0, 0),
                                         call(self.hwnd, 0x80, 1, 0)])
        api.refresh.assert_called_once_with(self.hwnd, None, 0, 0, 0, 0, 0x0037)

    def test_failed_refresh_can_be_retried_without_rewriting_existing_style(self):
        api = self.frame_api()
        api.get_style.side_effect = [0x00040100, 0x00040101]
        api.refresh.side_effect = [False, True]
        with patch.object(appearance, '_load_frame_api', return_value=api):
            self.assertFalse(appearance.hide_caption_icon(self.hwnd))
            self.assertTrue(appearance.hide_caption_icon(self.hwnd))
        api.set_style.assert_called_once_with(self.hwnd, -20, 0x00040101)
        self.assertEqual(api.clear_icon.call_count, 4)
        self.assertEqual(api.refresh.call_count, 2)

    def test_zero_previous_style_and_zero_set_result_can_be_success(self):
        api = self.frame_api(style=0)
        api.set_style.return_value = 0
        with patch.object(appearance, '_load_frame_api', return_value=api), \
                patch.object(appearance.C, 'get_last_error', return_value=0):
            self.assertTrue(appearance.hide_caption_icon(self.hwnd))
        api.set_style.assert_called_once_with(self.hwnd, -20, 1)
        api.refresh.assert_called_once()

    def test_failed_style_read_does_not_mutate_anything(self):
        api = self.frame_api(style=0)
        with patch.object(appearance, '_load_frame_api', return_value=api), \
                patch.object(appearance.C, 'get_last_error', return_value=5):
            self.assertFalse(appearance.hide_caption_icon(self.hwnd))
        api.set_style.assert_not_called()
        api.clear_icon.assert_not_called()
        api.refresh.assert_not_called()

    def test_failed_style_write_does_not_refresh_the_frame(self):
        api = self.frame_api()
        api.set_style.return_value = 0
        with patch.object(appearance, '_load_frame_api', return_value=api), \
                patch.object(appearance.C, 'get_last_error', return_value=5):
            self.assertFalse(appearance.hide_caption_icon(self.hwnd))
        api.clear_icon.assert_not_called()
        api.refresh.assert_not_called()

    def test_frame_bindings_keep_large_hwnd_and_icon_messages_pointer_safe(self):
        appearance._load_frame_api.cache_clear()
        getter, setter, clearer, refresher = Mock(), Mock(), Mock(), Mock()
        user = SimpleNamespace(GetWindowLongPtrW=getter, SetWindowLongPtrW=setter,
                               GetWindowLongW=getter, SetWindowLongW=setter,
                               SendMessageW=clearer, SetWindowPos=refresher)
        try:
            with patch.object(appearance.C, 'WinDLL', return_value=user):
                api = appearance._load_frame_api()
            self.assertIs(api.get_style, getter)
            self.assertEqual(getter.argtypes, [W.HWND, C.c_int])
            self.assertIs(getter.restype, C.c_ssize_t)
            self.assertEqual(setter.argtypes, [W.HWND, C.c_int, C.c_ssize_t])
            self.assertIs(setter.restype, C.c_ssize_t)
            self.assertEqual(clearer.argtypes, [W.HWND, W.UINT, W.WPARAM, W.LPARAM])
            self.assertIs(clearer.restype, C.c_ssize_t)
            self.assertEqual(refresher.argtypes,
                             [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT])
        finally:
            appearance._load_frame_api.cache_clear()

    def test_frame_refresh_failure_and_native_exception_return_false(self):
        api = self.frame_api()
        api.refresh.return_value = False
        with patch.object(appearance, '_load_frame_api', return_value=api):
            self.assertFalse(appearance.hide_caption_icon(self.hwnd))
        api.get_style.side_effect = OSError('unavailable')
        with patch.object(appearance, '_load_frame_api', return_value=api):
            self.assertFalse(appearance.hide_caption_icon(self.hwnd))

    def test_missing_frame_api_or_zero_handle_returns_false(self):
        with patch.object(appearance, '_load_frame_api', return_value=None):
            self.assertFalse(appearance.hide_caption_icon(self.hwnd))
        with patch.object(appearance, '_load_frame_api') as loader:
            self.assertFalse(appearance.hide_caption_icon(0))
        loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
