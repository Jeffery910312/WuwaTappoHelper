"""Count input events locally without storing text or an event history.

Only constructing InputMonitor installs hooks. Snapshot totals include injected
SendInput events, and key autorepeat is suppressed until its matching key-up.
"""
import ctypes as C
from ctypes import wintypes as W
import threading

u32 = C.WinDLL('user32', use_last_error=True)
k32 = C.WinDLL('kernel32', use_last_error=True)
LRESULT = C.c_ssize_t
HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
MOUSE_DOWN = frozenset((0x0201, 0x0204, 0x0207, 0x020B))


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [('vkCode', W.DWORD), ('scanCode', W.DWORD), ('flags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class MSLLHOOKSTRUCT(C.Structure):
    _fields_ = [('pt', W.POINT), ('mouseData', W.DWORD), ('flags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


def _signature(library, name, args, result):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result


_signature(u32, 'SetWindowsHookExW', [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD], W.HANDLE)
_signature(u32, 'UnhookWindowsHookEx', [W.HANDLE], W.BOOL)
_signature(u32, 'CallNextHookEx', [W.HANDLE, C.c_int, W.WPARAM, W.LPARAM], LRESULT)
_signature(u32, 'PeekMessageW', [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT], W.BOOL)
_signature(u32, 'TranslateMessage', [C.POINTER(W.MSG)], W.BOOL)
_signature(u32, 'DispatchMessageW', [C.POINTER(W.MSG)], LRESULT)
_signature(k32, 'GetModuleHandleW', [W.LPCWSTR], W.HMODULE)


class InputMonitor:
    def __init__(self):
        self.error = None
        self._lock = threading.Lock()
        self._keyboard = self._mouse = 0
        # Transient state only, for autorepeat suppression. No key history is kept.
        self._pressed = set()
        self._hooks = []
        self._closed = threading.Event()
        self.ready = threading.Event()
        # Hold Python callback objects for at least the full lifetime of both hooks.
        self._keyboard_proc = HOOKPROC(self._on_keyboard)
        self._mouse_proc = HOOKPROC(self._on_mouse)
        self._thread = threading.Thread(target=self._run, name='input-count-monitor', daemon=True)
        self._thread.start()
        if not self.ready.wait(2):
            self.error = TimeoutError('初始化輸入計數逾時。')
            self.close()

    @property
    def running(self):
        return self._thread.is_alive() and not self._closed.is_set() and self.error is None

    def snapshot(self):
        with self._lock:
            return dict(total=self._keyboard + self._mouse,
                        keyboard=self._keyboard, mouse=self._mouse)

    def _on_keyboard(self, code, message, pointer):
        try:
            if code >= 0 and pointer and message in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
                event = C.cast(pointer, C.POINTER(KBDLLHOOKSTRUCT)).contents
                with self._lock:
                    if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if event.vkCode not in self._pressed:
                            self._pressed.add(event.vkCode)
                            self._keyboard += 1
                    else:
                        self._pressed.discard(event.vkCode)
        except Exception as exc:
            self.error = exc
        return u32.CallNextHookEx(None, code, message, pointer)

    def _on_mouse(self, code, message, pointer):
        try:
            if code >= 0 and message in MOUSE_DOWN:
                with self._lock:
                    self._mouse += 1
        except Exception as exc:
            self.error = exc
        return u32.CallNextHookEx(None, code, message, pointer)

    def _run(self):
        try:
            module = k32.GetModuleHandleW(None)
            if not module:
                raise C.WinError(C.get_last_error())
            for kind, callback in ((WH_KEYBOARD_LL, self._keyboard_proc), (WH_MOUSE_LL, self._mouse_proc)):
                handle = u32.SetWindowsHookExW(kind, callback, module, 0)
                if not handle:
                    raise C.WinError(C.get_last_error())
                self._hooks.append(handle)
            self.ready.set()
            message = W.MSG()
            while not self._closed.is_set():
                while not self._closed.is_set() and u32.PeekMessageW(C.byref(message), None, 0, 0, 1):
                    if message.message == 0x0012:  # WM_QUIT
                        self._closed.set()
                        break
                    u32.TranslateMessage(C.byref(message))
                    u32.DispatchMessageW(C.byref(message))
                self._closed.wait(.01)
        except Exception as exc:
            self.error = exc
        finally:
            for handle in reversed(self._hooks):
                try:
                    if not u32.UnhookWindowsHookEx(handle) and self.error is None:
                        self.error = C.WinError(C.get_last_error())
                except Exception as exc:
                    if self.error is None:
                        self.error = exc
            self._hooks.clear()
            with self._lock:
                self._pressed.clear()
            self.ready.set()

    def close(self):
        self._closed.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
