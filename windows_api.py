"""Physical-pixel Win32 access; no memory injection or application network calls."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import queue
import threading
import time

u32 = C.WinDLL('user32', use_last_error=True)
k32 = C.WinDLL('kernel32', use_last_error=True)
a32 = C.WinDLL('advapi32', use_last_error=True)


def signature(lib, name, args, result):
    fn = getattr(lib, name)
    fn.argtypes, fn.restype = args, result
    return fn


signature(u32, 'SetProcessDpiAwarenessContext', [W.HANDLE], W.BOOL)
u32.SetProcessDpiAwarenessContext(C.c_void_p(-4))
signature(u32, 'GetWindowTextW', [W.HWND, W.LPWSTR, C.c_int], C.c_int)
signature(u32, 'GetWindowThreadProcessId', [W.HWND, C.POINTER(W.DWORD)], W.DWORD)
signature(u32, 'GetWindowRect', [W.HWND, C.POINTER(W.RECT)], W.BOOL)
signature(u32, 'IsWindowVisible', [W.HWND], W.BOOL)
signature(u32, 'IsIconic', [W.HWND], W.BOOL)
signature(u32, 'GetAncestor', [W.HWND, W.UINT], W.HWND)
signature(u32, 'WindowFromPoint', [W.POINT], W.HWND)
signature(u32, 'ScreenToClient', [W.HWND, C.POINTER(W.POINT)], W.BOOL)
signature(u32, 'PostMessageW', [W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL)
signature(u32, 'GetCursorPos', [C.POINTER(W.POINT)], W.BOOL)
signature(u32, 'SetCursorPos', [C.c_int, C.c_int], W.BOOL)
signature(u32, 'GetAsyncKeyState', [C.c_int], C.c_short)
signature(u32, 'GetForegroundWindow', [], W.HWND)
signature(u32, 'OpenInputDesktop', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
signature(u32, 'CloseDesktop', [W.HANDLE], W.BOOL)
signature(u32, 'GetSystemMetrics', [C.c_int], C.c_int)
signature(u32, 'RegisterHotKey', [W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL)
signature(u32, 'UnregisterHotKey', [W.HWND, C.c_int], W.BOOL)
signature(u32, 'PeekMessageW', [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT], W.BOOL)
signature(k32, 'OpenProcess', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
signature(k32, 'CloseHandle', [W.HANDLE], W.BOOL)
signature(k32, 'CreateMutexW', [C.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE)
signature(k32, 'QueryFullProcessImageNameW', [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL)
signature(k32, 'GetTickCount', [], W.DWORD)
signature(a32, 'OpenProcessToken', [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], W.BOOL)
signature(a32, 'GetTokenInformation', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)], W.BOOL)
signature(a32, 'GetSidSubAuthorityCount', [C.c_void_p], C.POINTER(C.c_ubyte))
signature(a32, 'GetSidSubAuthority', [C.c_void_p, W.DWORD], C.POINTER(W.DWORD))


class SidAndAttributes(C.Structure):
    _fields_ = [('sid', C.c_void_p), ('attributes', W.DWORD)]


def _process_input_access(pid):
    """Read integrity and UIAccess without requesting any mutation rights."""
    process = k32.OpenProcess(0x1000, False, pid)
    if not process:
        raise C.WinError(C.get_last_error())
    token = W.HANDLE()
    try:
        if not a32.OpenProcessToken(process, 0x0008, C.byref(token)):
            raise C.WinError(C.get_last_error())
        try:
            size = W.DWORD()
            a32.GetTokenInformation(token, 25, None, 0, C.byref(size))
            if size.value < C.sizeof(SidAndAttributes):
                raise OSError('無法讀取程序完整性層級。')
            label = C.create_string_buffer(size.value)
            if not a32.GetTokenInformation(token, 25, label, size.value, C.byref(size)):
                raise C.WinError(C.get_last_error())
            sid = C.cast(label, C.POINTER(SidAndAttributes)).contents.sid
            if not sid:
                raise OSError('程序完整性層級沒有有效的 SID。')
            count = a32.GetSidSubAuthorityCount(sid)
            if not count or not count.contents.value:
                raise OSError('程序完整性層級沒有有效的 SID。')
            authority = a32.GetSidSubAuthority(sid, count.contents.value - 1)
            if not authority:
                raise OSError('無法讀取程序完整性層級。')
            integrity = authority.contents.value
            ui_access = W.DWORD()
            if not a32.GetTokenInformation(token, 26, C.byref(ui_access), C.sizeof(ui_access), C.byref(size)):
                raise C.WinError(C.get_last_error())
            return integrity, bool(ui_access.value)
        finally:
            k32.CloseHandle(token)
    finally:
        k32.CloseHandle(process)


@lru_cache(maxsize=1)
def _current_input_access():
    return _process_input_access(os.getpid())


def ensure_input_access(target):
    """Stop before input when Windows would block a higher-integrity target."""
    try:
        own_integrity, ui_access = _current_input_access()
        target_integrity, _ = _process_input_access(target.pid)
    except OSError as exc:
        raise RuntimeError('無法確認 Wuwa Tappo 的輸入權限，自動領箱已暫停。請確認遊戲仍在執行，再重新開始領箱。') from exc
    if target_integrity > own_integrity and not ui_access:
        raise RuntimeError('Wuwa Tappo 的權限高於助手，寶箱點擊會被 Windows 阻擋，自動領箱已暫停。請關閉助手，再執行 Start.bat 並允許管理員權限。')


class LastInput(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('dwTime', W.DWORD)]


signature(u32, 'GetLastInputInfo', [C.POINTER(LastInput)], W.BOOL)


class MouseInput(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class KeyboardInput(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                ('time', W.DWORD), ('dwExtraInfo', C.c_size_t)]


class HardwareInput(C.Structure):
    _fields_ = [('uMsg', W.DWORD), ('wParamL', W.WORD), ('wParamH', W.WORD)]


class InputUnion(C.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput), ('hi', HardwareInput)]


class Input(C.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', W.DWORD), ('data', InputUnion)]


signature(u32, 'SendInput', [W.UINT, C.POINTER(Input), C.c_int], W.UINT)
EnumProc = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
signature(u32, 'EnumWindows', [EnumProc, W.LPARAM], W.BOOL)


@dataclass(frozen=True)
class Target:
    hwnd: int
    pid: int
    rect: tuple


class InstanceLock:
    """Keep one GUI per project and Windows session while its handle is open."""

    def __init__(self, project_dir=None):
        directory = Path(project_dir) if project_dir is not None else Path(__file__).parent
        normalized = os.path.normcase(str(directory.resolve()))
        digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        self.name = f'Local\\WuwaTappoClicker_{digest}'
        self.handle = None

    @property
    def acquired(self):
        return self.handle is not None

    def acquire(self):
        if self.acquired:
            return True
        C.set_last_error(0)
        handle = k32.CreateMutexW(None, False, self.name)
        error = C.get_last_error()
        if not handle:
            raise C.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS
            k32.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def close(self):
        if self.handle is not None:
            k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError('此專案的 Wuwa Tappo 寶箱助手已在執行。')
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def title(hwnd):
    buf = C.create_unicode_buffer(512)
    u32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def identity(hwnd):
    pid = W.DWORD()
    u32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    handle = k32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return pid.value, ''
    try:
        buf, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
            return pid.value, ''
        return pid.value, Path(buf.value).name.lower()
    finally:
        k32.CloseHandle(handle)


def targets():
    found = []
    @EnumProc
    def visit(hwnd, _):
        if title(hwnd) == 'Wuwa Tappo' and u32.IsWindowVisible(hwnd) and not u32.IsIconic(hwnd):
            pid, exe = identity(hwnd)
            rect = W.RECT()
            if exe == 'wuwatappo.exe' and u32.GetWindowRect(hwnd, C.byref(rect)):
                if rect.right > rect.left and rect.bottom > rect.top:
                    found.append(Target(hwnd, pid, (rect.left, rect.top, rect.right, rect.bottom)))
        return True
    u32.EnumWindows(visit, 0)
    return found


def cursor():
    point = W.POINT()
    if not u32.GetCursorPos(C.byref(point)):
        raise C.WinError(C.get_last_error())
    return point.x, point.y


def idle_seconds():
    info = LastInput(C.sizeof(LastInput), 0)
    if not u32.GetLastInputInfo(C.byref(info)):
        return 0
    return ((k32.GetTickCount() - info.dwTime) & 0xffffffff) / 1000


def held_keys():
    return any(u32.GetAsyncKeyState(k) & 0x8000 for k in (1, 2, 4, 0x10, 0x11, 0x12, 0x5B, 0x5C))


def tap_key(key, allowed):
    """Send one complete uncommon key press without moving the mouse."""
    if key not in {f'F{i}' for i in range(13, 25)}:
        raise ValueError('累積按鍵請選 F13～F24。')
    vk = 0x70 + int(key[1:]) - 1
    if not allowed() or held_keys() or u32.GetAsyncKeyState(vk) & 0x8000:
        return False
    desktop = u32.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return False
    u32.CloseDesktop(desktop)
    if not u32.GetForegroundWindow() or not allowed():
        return False
    events = (Input * 2)()
    for event in events:
        event.type = 1
        event.ki.wVk = vk
    events[1].ki.dwFlags = 0x0002
    count = u32.SendInput(2, events, C.sizeof(Input))
    if count != 2:
        release = events[1]
        u32.SendInput(1, C.byref(release), C.sizeof(Input))
        raise RuntimeError('Windows 拒絕鍵盤輸入，已暫停。')
    return True


def owns_point(target, x, y):
    left, top, right, bottom = target.rect
    if not (left <= x < right and top <= y < bottom):
        return False
    if (title(target.hwnd) != 'Wuwa Tappo' or not u32.IsWindowVisible(target.hwnd)
            or u32.IsIconic(target.hwnd)):
        return False
    main_pid, main_exe = identity(target.hwnd)
    rect = W.RECT()
    if (main_pid != target.pid or main_exe != 'wuwatappo.exe'
            or not u32.GetWindowRect(target.hwnd, C.byref(rect))
            or (rect.left, rect.top, rect.right, rect.bottom) != target.rect):
        return False
    hit = u32.WindowFromPoint(W.POINT(x, y))
    root = u32.GetAncestor(hit, 2) if hit else 0
    # Wuwa Tappo places its pet's input surface in a separate top-level overlay.
    # Only that exact overlay from the already validated process is acceptable.
    if not root or (root != target.hwnd and title(root) != 'DesktopCatInput'):
        return False
    if not u32.IsWindowVisible(root) or u32.IsIconic(root):
        return False
    pid, exe = identity(root)
    return pid == target.pid and exe == 'wuwatappo.exe'


def click(target, x, y, allowed):
    if not allowed():
        return False
    ensure_input_access(target)
    if held_keys() or idle_seconds() < 1.2 or not owns_point(target, x, y):
        return False
    before = cursor()
    if not u32.SetCursorPos(x, y):
        raise C.WinError(C.get_last_error())
    down_sent = False
    try:
        time.sleep(.08)
        if not allowed() or cursor() != (x, y) or held_keys() or not owns_point(target, x, y):
            return False
        # Include the point in the events. Restoring the cursor immediately after
        # a position-less down/up can otherwise retarget queued input.
        vx, vy = u32.GetSystemMetrics(76), u32.GetSystemMetrics(77)
        vw, vh = u32.GetSystemMetrics(78), u32.GetSystemMetrics(79)
        if vw <= 1 or vh <= 1:
            raise RuntimeError('無法取得螢幕座標，已暫停。')
        dx = max(0, min(65535, round((x-vx)*65535/(vw-1))))
        dy = max(0, min(65535, round((y-vy)*65535/(vh-1))))
        down, up = Input(), Input()
        for event, button in ((down, 0x0002), (up, 0x0004)):
            event.mi.dx, event.mi.dy = dx, dy
            event.mi.dwFlags = 0x8000 | 0x4000 | 0x0001 | button
        if u32.SendInput(1, C.byref(down), C.sizeof(Input)) != 1:
            raise RuntimeError('Windows 拒絕滑鼠輸入，已暫停。')
        down_sent = True
        time.sleep(.06)
        moved = cursor()
        if abs(moved[0]-x) > 1 or abs(moved[1]-y) > 1:
            return False
        if u32.SendInput(1, C.byref(up), C.sizeof(Input)) != 1:
            raise RuntimeError('Windows 拒絕滑鼠放開事件，已暫停。')
        down_sent = False
        time.sleep(.12)
        return True
    finally:
        if down_sent:
            release = Input()
            release.mi.dwFlags = 0x0004
            u32.SendInput(1, C.byref(release), C.sizeof(Input))
        position = cursor()
        if abs(position[0]-x) <= 1 and abs(position[1]-y) <= 1:
            u32.SetCursorPos(*before)


def _background_recipient(target, x, y):
    """Resolve only the exposed game input window; never activate it."""
    if not owns_point(target, x, y):
        return None
    hwnd = u32.WindowFromPoint(W.POINT(x, y))
    root = u32.GetAncestor(hwnd, 2) if hwnd else 0
    if (not root or (root != target.hwnd and title(root) != 'DesktopCatInput')
            or identity(root) != (target.pid, 'wuwatappo.exe')
            or identity(hwnd) != (target.pid, 'wuwatappo.exe')):
        return None
    pid = W.DWORD()
    thread = u32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    if not thread or pid.value != target.pid:
        return None
    return hwnd, root, thread


def _same_background_recipient(target, recipient):
    """A release stays addressed to the original process and input surface."""
    hwnd, root, thread = recipient
    pid = W.DWORD()
    return (u32.GetWindowThreadProcessId(hwnd, C.byref(pid)) == thread
            and pid.value == target.pid
            and identity(hwnd) == (target.pid, 'wuwatappo.exe')
            and u32.GetAncestor(hwnd, 2) == root
            and identity(root) == (target.pid, 'wuwatappo.exe')
            and (root == target.hwnd or title(root) == 'DesktopCatInput'))


def _post_mouse_message(hwnd, message, buttons, point):
    if not u32.PostMessageW(hwnd, message, buttons, point):
        error = C.get_last_error()
        raise RuntimeError(f'Windows 拒絕背景滑鼠訊息（錯誤 {error}），自動領箱已暫停。')


def click_background(target, x, y, allowed):
    """Post a click to one game window without moving the system cursor.

    True means messages were queued, not that the game accepted the claim.
    The caller must verify the chest and counter through the screen.
    """
    if not allowed():
        return False
    ensure_input_access(target)
    recipient = _background_recipient(target, x, y)
    if recipient is None:
        return False
    hwnd, _, _ = recipient
    point = W.POINT(x, y)
    if not u32.ScreenToClient(hwnd, C.byref(point)):
        raise RuntimeError('無法換算背景點擊座標，自動領箱已暫停。')
    if not (-32768 <= point.x <= 32767 and -32768 <= point.y <= 32767):
        raise RuntimeError('背景點擊座標超出 Windows 訊息範圍，自動領箱已暫停。')
    packed = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
    if not allowed() or _background_recipient(target, x, y) != recipient:
        return False
    _post_mouse_message(hwnd, 0x0200, 0, packed)  # WM_MOUSEMOVE
    # Cancellation or an overlay change after hover must not become a click.
    if not allowed() or _background_recipient(target, x, y) != recipient:
        return False
    down_sent = False
    try:
        _post_mouse_message(hwnd, 0x0201, 0x0001, packed)  # WM_LBUTTONDOWN
        down_sent = True
        time.sleep(.06)
        return bool(allowed())
    finally:
        if down_sent:
            # Do not consult allowed() or hit testing for cleanup: pausing or
            # covering the chest must still release the original game window.
            if not _same_background_recipient(target, recipient):
                raise RuntimeError('背景輸入視窗已變更，無法確認點擊結果，自動領箱已暫停。')
            _post_mouse_message(hwnd, 0x0202, 0, packed)  # WM_LBUTTONUP


class Hotkeys:
    def __init__(self):
        self.registered = []
        self.error = None
        self._messages = queue.Queue()
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name='global-hotkeys', daemon=True)
        self._thread.start()
        if not self._ready.wait(2):
            self.close()
            raise RuntimeError('初始化全域快捷鍵逾時。')

    def _run(self):
        # Thread-registered hotkeys post WM_HOTKEY to that thread's queue.
        # Keep it separate from Tk, whose event loop may otherwise consume it.
        try:
            for ident, key in ((1, 0x74),):  # F5 only; avoid Wuwa Tappo's F7-F9.
                if u32.RegisterHotKey(None, ident, 0x4000, key):
                    self.registered.append(ident)
            self._ready.set()
            while not self._closed.is_set():
                message = W.MSG()
                while not self._closed.is_set() and u32.PeekMessageW(C.byref(message), None, 0x0312, 0x0312, 1):
                    self._messages.put(int(message.wParam))
                self._closed.wait(.01)
        except Exception as exc:
            self.error = exc
        finally:
            self._ready.set()
            for ident in self.registered:
                u32.UnregisterHotKey(None, ident)

    def poll(self):
        result = []
        while True:
            try:
                result.append(self._messages.get_nowait())
            except queue.Empty:
                break
        return result

    def close(self):
        self._closed.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
