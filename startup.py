"""Request administrator access before starting the Windows GUI."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import subprocess
import sys


ELEVATED_FLAG = '--wuwa-elevated'


def ensure_admin(*, relaunched=False):
    """Return True here, or False after handing startup to an elevated child."""
    shell = C.WinDLL('shell32', use_last_error=True)
    shell.IsUserAnAdmin.argtypes = []
    shell.IsUserAnAdmin.restype = W.BOOL
    if shell.IsUserAnAdmin():
        return True
    if relaunched:
        raise PermissionError('未取得管理員權限，寶箱助手未啟動。請重新執行 Start.bat 並允許管理員權限。')

    shell.ShellExecuteW.argtypes = [W.HWND, W.LPCWSTR, W.LPCWSTR,
                                    W.LPCWSTR, W.LPCWSTR, C.c_int]
    shell.ShellExecuteW.restype = W.HINSTANCE
    directory = Path(__file__).resolve().parent
    parameters = subprocess.list2cmdline([
        str(directory / 'Launch.pyw'), ELEVATED_FLAG, *sys.argv[1:],
    ])
    C.set_last_error(0)
    result = shell.ShellExecuteW(
        None, 'runas', sys.executable, parameters, str(directory), 1,
    ) or 0
    if result <= 32:
        error = C.get_last_error()
        if error == 1223:  # ERROR_CANCELLED: the user declined the UAC prompt.
            raise PermissionError('已取消管理員授權，寶箱助手未啟動。需要使用時請重新執行 Start.bat。')
        raise C.WinError(error or result)
    return False
