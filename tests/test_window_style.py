"""Native frame styling boundaries; no real windows or desktop changes."""
import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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


if __name__ == '__main__':
    unittest.main()
