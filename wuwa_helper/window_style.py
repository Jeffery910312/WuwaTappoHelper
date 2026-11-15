"""Per-window native DWM appearance; no changes to Windows theme settings.

Windows 11 supports the caption, border and corner attributes used here.
Older Windows versions keep their normal window frame when an attribute is
unsupported. Native resizing, snapping and caption buttons remain untouched.
"""
import ctypes as C
from ctypes import wintypes as W
from functools import lru_cache
import re
from types import SimpleNamespace


DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWCP_ROUND = 2
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_DLGMODALFRAME = 0x00000001
FRAME_REFRESH = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1


def set_app_identity():
    """Give this application its own taskbar group before creating its UI."""
    try:
        function = C.WinDLL('shell32', use_last_error=True).SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [W.LPCWSTR]
        function.restype = C.c_int32
        return function('WuwaTappo.Helper.Desktop') >= 0
    except (AttributeError, OSError):
        return False


def colorref(color):
    """Convert a CSS #RRGGBB color to Win32's 0x00BBGGRR COLORREF."""
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('Window colors must use #RRGGBB notation.')
    red, green, blue = (int(color[offset:offset+2], 16) for offset in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


@lru_cache(maxsize=1)
def _load_dwm():
    try:
        function = C.WinDLL('dwmapi', use_last_error=True).DwmSetWindowAttribute
        function.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
        function.restype = C.c_int32  # HRESULT stays signed and 32-bit.
        return function
    except (AttributeError, OSError):
        return None


@lru_cache(maxsize=1)
def _load_get_ancestor():
    try:
        function = C.WinDLL('user32', use_last_error=True).GetAncestor
        function.argtypes = [W.HWND, W.UINT]
        function.restype = W.HWND
        return function
    except (AttributeError, OSError):
        return None


def get_frame_handle(widget_hwnd):
    """Resolve Tk's client HWND to its actual top-level native frame."""
    if not widget_hwnd:
        return 0
    ancestor = _load_get_ancestor()
    if ancestor is None:
        return 0
    try:
        return int(ancestor(widget_hwnd, GA_ROOT) or 0)
    except OSError:
        return 0


@lru_cache(maxsize=1)
def _load_frame_api():
    try:
        user = C.WinDLL('user32', use_last_error=True)
        suffix = 'PtrW' if C.sizeof(C.c_void_p) == 8 else 'W'
        get_style = getattr(user, 'GetWindowLong' + suffix)
        get_style.argtypes, get_style.restype = [W.HWND, C.c_int], C.c_ssize_t
        set_style = getattr(user, 'SetWindowLong' + suffix)
        set_style.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
        set_style.restype = C.c_ssize_t
        refresh = user.SetWindowPos
        refresh.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        refresh.restype = W.BOOL
        clear_icon = user.SendMessageW
        clear_icon.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        clear_icon.restype = C.c_ssize_t
        return SimpleNamespace(get_style=get_style, set_style=set_style,
                               clear_icon=clear_icon, refresh=refresh)
    except (AttributeError, OSError):
        return None


def hide_caption_icon(hwnd):
    """Remove the native system-menu icon without changing resize/button styles.

    A dialog-style caption omits the icon. Unlike a transparent HICON, it has
    no bitmap for Windows to render as an opaque square. This changes only
    this window's extended style and leaves its regular styles untouched.
    """
    if not hwnd:
        return False
    api = _load_frame_api()
    if api is None:
        return False
    try:
        C.set_last_error(0)
        previous = api.get_style(hwnd, GWL_EXSTYLE)
        if previous == 0 and C.get_last_error():
            return False
        updated = previous | WS_EX_DLGMODALFRAME
        if updated != previous:
            C.set_last_error(0)
            result = api.set_style(hwnd, GWL_EXSTYLE, updated)
            if result == 0 and C.get_last_error():
                return False
        # Clear Tk's per-window icons as well as suppressing the caption icon.
        # Do not destroy the returned handles: Tk retains ownership of them.
        api.clear_icon(hwnd, WM_SETICON, ICON_SMALL, 0)
        api.clear_icon(hwnd, WM_SETICON, ICON_BIG, 0)
        # FRAMECHANGED refreshes cached non-client styling; the other flags
        # keep size, location, stacking and foreground focus unchanged.
        return bool(api.refresh(hwnd, None, 0, 0, 0, 0, FRAME_REFRESH))
    except OSError:
        return False


def apply_window_style(hwnd, *, dark, caption_color, border_color, text_color):
    """Try native title/border colors and rounded corners for one window.

    Return True only when every attribute is supported. Failed or unavailable
    attributes are harmless: the existing native decoration remains in place.
    """
    if not hwnd:
        return False
    # Validate all colors before mutating any window attribute.
    caption, border, text = map(colorref, (caption_color, border_color, text_color))
    setter = _load_dwm()
    if setter is None:
        return False
    attributes = (
        (DWMWA_USE_IMMERSIVE_DARK_MODE, C.c_int32(bool(dark))),
        (DWMWA_WINDOW_CORNER_PREFERENCE, C.c_int32(DWMWCP_ROUND)),
        (DWMWA_BORDER_COLOR, W.DWORD(border)),
        (DWMWA_CAPTION_COLOR, W.DWORD(caption)),
        (DWMWA_TEXT_COLOR, W.DWORD(text)),
    )
    supported = True
    for attribute, value in attributes:
        try:
            result = setter(hwnd, attribute, C.byref(value), C.sizeof(value))
            supported = C.c_int32(result).value >= 0 and supported
        except OSError:
            supported = False
    return supported
