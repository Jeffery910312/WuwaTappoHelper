"""Fresh HWND-bound Windows Graphics Capture snapshots, never desktop fallback."""
import ctypes as C
from ctypes import wintypes as W
import math
from numbers import Integral
import threading

import numpy as np
from PIL import Image


class CaptureError(RuntimeError):
    """The requested game window could not supply a trustworthy snapshot."""


def _target_is_current(target):
    from . import windows_api as api
    if (api.title(target.hwnd) != 'Wuwa Tappo'
            or not api.u32.IsWindowVisible(target.hwnd)
            or api.u32.IsIconic(target.hwnd)
            or api.identity(target.hwnd) != (target.pid, 'wuwatappo.exe')):
        return False
    rect = W.RECT()
    return (bool(api.u32.GetWindowRect(target.hwnd, C.byref(rect)))
            and (rect.left, rect.top, rect.right, rect.bottom) == tuple(target.rect))


def _rectangle(rect):
    if len(rect) != 4 or any(not isinstance(v, Integral) or isinstance(v, bool) for v in rect):
        raise CaptureError('Capture rectangles require four integer coordinates.')
    left, top, right, bottom = map(int, rect)
    if right <= left or bottom <= top:
        raise CaptureError('Capture rectangles require positive width and height.')
    return left, top, right, bottom


class WindowGrabber:
    """Capture a fresh frame from exactly one validated game HWND per request.

    Each grab starts and closes its own session. No old image, background
    desktop pixels, input injection, or persistent capture stream is used.
    """
    def __init__(self, factory=None, validator=None, timeout=3.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Capture timeout must be a positive finite duration.')
        self.factory = factory
        self.validator = validator if validator is not None else _target_is_current
        self.timeout = float(timeout)

    def grab(self, target, rect):
        bounds = _rectangle(target.rect)
        left, top, right, bottom = _rectangle(rect)
        if not (bounds[0] <= left < right <= bounds[2]
                and bounds[1] <= top < bottom <= bounds[3]):
            raise CaptureError('Capture region must be inside the game window.')
        metadata = (target.hwnd, target.pid, bounds)
        if not target.hwnd or not target.pid:
            raise CaptureError('Invalid game window identity.')

        def validate():
            if ((target.hwnd, target.pid, tuple(target.rect)) != metadata
                    or not self.validator(target)):
                raise CaptureError('The game window changed, closed, or became minimized.')

        try:
            validate()
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError('Unable to validate the game window.') from exc

        factory = self.factory
        if factory is None:
            try:
                from windows_capture import WindowsCapture
                factory = WindowsCapture
            except ImportError as exc:
                raise CaptureError('Window capture is unavailable. Run Setup.bat to install windows-capture.') from exc

        completed = threading.Event()
        lock = threading.Lock()
        outcome = {}
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        x1, y1, x2, y2 = left-bounds[0], top-bounds[1], right-bounds[0], bottom-bounds[1]

        def finish(**result):
            with lock:
                if not completed.is_set():
                    outcome.update(result)
                    completed.set()

        def on_frame_arrived(frame, _capture_control):
            if completed.is_set():
                return
            try:
                pixels = np.asarray(frame.frame_buffer)
                if (pixels.dtype != np.uint8 or pixels.shape != (height, width, 4)
                        or frame.width != width or frame.height != height):
                    raise CaptureError('Captured frame dimensions do not match the game window.')
                # Copy only the requested ROI, not the full transparent surface.
                roi = pixels[y1:y2, x1:x2]
                rgb = np.array(roi[:, :, 2::-1], dtype=np.uint8, copy=True, order='C')
                rgb[roi[:, :, 3] == 0] = 0
                finish(image=Image.fromarray(rgb))
            except Exception as exc:
                finish(error=exc)

        def on_closed():
            finish(error=CaptureError('The game window closed before a frame arrived.'))

        capture = control = None
        failed = False
        try:
            capture = factory(window_hwnd=target.hwnd, cursor_capture=False)
            capture.frame_handler = on_frame_arrived
            capture.closed_handler = on_closed
            control = capture.start_free_threaded()
            if not completed.wait(self.timeout):
                raise CaptureError('Timed out waiting for a fresh game window frame.')
            if 'error' in outcome:
                raise outcome['error']
            validate()
            return outcome['image']
        except CaptureError:
            failed = True
            raise
        except Exception as exc:
            failed = True
            raise CaptureError(f'Window capture failed: {exc}') from exc
        finally:
            try:
                # Native stop joins its callback thread; never hold the callback lock.
                if control is not None:
                    try:
                        control.stop()
                    except Exception as exc:
                        if not failed:
                            raise CaptureError(f'Unable to stop window capture: {exc}') from exc
            finally:
                if capture is not None:
                    capture.frame_handler = None
                    capture.closed_handler = None
                    # windows-capture 2.0.1 NativeWindowsCapture owns bound
                    # callbacks to its Python wrapper. Its public `capture`
                    # attribute points back to that native config, forming a
                    # cycle which PyO3's GC does not traverse. Detach it only
                    # after the session has been stopped, releasing both the
                    # native configuration and per-request image closures.
                    if hasattr(capture, 'capture'):
                        capture.capture = None
                outcome.clear()