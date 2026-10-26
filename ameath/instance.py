import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile


class SingleInstance:
    """Filesystem lock works in Windows sessions with restricted pipe access."""
    def __init__(self, directory: Path, activate_existing: bool = True):
        self.lock = QLockFile(str(directory / "instance.lock"))
        self.lock.setStaleLockTime(0)
        self.primary = self.lock.tryLock(80)
        if not self.primary:
            if self.lock.error() != QLockFile.LockError.LockFailedError:
                raise OSError("无法创建 Ameath 运行锁，请确认数据目录可以写入。")
            if activate_existing and sys.platform == "win32":
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
                user32.FindWindowW.restype = ctypes.c_void_p
                user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
                user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                handle = user32.FindWindowW(None, "AmeathAgent · 陪你一起读懂")
                if handle:
                    user32.ShowWindow(handle, 9)
                    user32.SetForegroundWindow(handle)

    def close(self):
        if self.primary:
            self.lock.unlock()
