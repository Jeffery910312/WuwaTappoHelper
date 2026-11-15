"""Copy only a requested physical-pixel screen rectangle using Windows GDI."""
import ctypes as C
from ctypes import wintypes as W
from numbers import Integral

from PIL import Image


user32 = C.WinDLL('user32', use_last_error=True)
gdi32 = C.WinDLL('gdi32', use_last_error=True)


def _signature(library, name, arguments, result):
    function = getattr(library, name)
    function.argtypes, function.restype = arguments, result
    return function


class BitmapInfoHeader(C.Structure):
    _fields_ = [('biSize', W.DWORD), ('biWidth', W.LONG), ('biHeight', W.LONG),
                ('biPlanes', W.WORD), ('biBitCount', W.WORD), ('biCompression', W.DWORD),
                ('biSizeImage', W.DWORD), ('biXPelsPerMeter', W.LONG), ('biYPelsPerMeter', W.LONG),
                ('biClrUsed', W.DWORD), ('biClrImportant', W.DWORD)]


class RGBQuad(C.Structure):
    _fields_ = [('blue', W.BYTE), ('green', W.BYTE), ('red', W.BYTE), ('reserved', W.BYTE)]


class BitmapInfo(C.Structure):
    _fields_ = [('bmiHeader', BitmapInfoHeader), ('bmiColors', RGBQuad * 1)]


_signature(user32, 'GetDC', [W.HWND], W.HDC)
_signature(user32, 'ReleaseDC', [W.HWND, W.HDC], C.c_int)
_signature(user32, 'SetThreadDpiAwarenessContext', [W.HANDLE], W.HANDLE)
_signature(gdi32, 'CreateCompatibleDC', [W.HDC], W.HDC)
_signature(gdi32, 'DeleteDC', [W.HDC], W.BOOL)
_signature(gdi32, 'CreateDIBSection', [W.HDC, C.POINTER(BitmapInfo), W.UINT,
                                    C.POINTER(C.c_void_p), W.HANDLE, W.DWORD], W.HBITMAP)
_signature(gdi32, 'SelectObject', [W.HDC, W.HGDIOBJ], W.HGDIOBJ)
_signature(gdi32, 'DeleteObject', [W.HGDIOBJ], W.BOOL)
_signature(gdi32, 'BitBlt', [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int,
                          W.HDC, C.c_int, C.c_int, W.DWORD], W.BOOL)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
HGDI_ERROR = C.c_void_p(-1).value


def _failure(operation):
    error = C.get_last_error()
    if error:
        return C.WinError(error)
    return OSError(f'{operation} failed while capturing the screen region')


def grab_region(rect):
    """Capture (left, top, right, bottom), including negative monitor offsets.

    The returned RGB image owns its pixel storage. GDI copies exactly the ROI;
    it never allocates a bitmap spanning the whole virtual desktop.
    """
    if len(rect) != 4 or any(not isinstance(value, Integral) or isinstance(value, bool) for value in rect):
        raise ValueError('Capture rectangle must contain four integer coordinates.')
    left, top, right, bottom = map(int, rect)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError('Capture rectangle must have positive width and height.')
    if any(not -(2**31) <= value < 2**31 for value in (left, top, right, bottom, width, height)):
        raise ValueError('Capture coordinates exceed Win32 integer range.')
    size = width * height * 4
    if size > 0xffffffff:
        raise ValueError('Capture rectangle is too large for a Windows bitmap.')

    # DPI awareness is scoped to the calling worker, and restored even on failure.
    dpi_context = user32.SetThreadDpiAwarenessContext(C.c_void_p(-4))
    screen_dc = memory_dc = bitmap = previous = None
    selected = False
    try:
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise _failure('GetDC')
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            raise _failure('CreateCompatibleDC')
        info = BitmapInfo()
        info.bmiHeader.biSize = C.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # A top-down DIB needs no row reversal.
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB, four-byte-aligned BGRX rows.
        info.bmiHeader.biSizeImage = size
        pixels = C.c_void_p()
        bitmap = gdi32.CreateDIBSection(screen_dc, C.byref(info), 0, C.byref(pixels), None, 0)
        if not bitmap or not pixels.value:
            raise _failure('CreateDIBSection')
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous or previous == HGDI_ERROR:
            raise _failure('SelectObject')
        selected = True
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc,
                           left, top, SRCCOPY | CAPTUREBLT):
            raise _failure('BitBlt')
        storage = (C.c_ubyte * size).from_address(pixels.value)
        return Image.frombuffer('RGB', (width, height), storage, 'raw', 'BGRX', 0, 1).copy()
    finally:
        if selected:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)
        if dpi_context:
            user32.SetThreadDpiAwarenessContext(dpi_context)
