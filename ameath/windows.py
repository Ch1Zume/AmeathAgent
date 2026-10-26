from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter


class GlobalHotkeys(QAbstractNativeEventFilter):
    """Register explicit actions, without keyboard or clipboard monitoring."""
    def __init__(self, app, explain, capture, pause_game=None):
        super().__init__()
        self.app = app
        self.callbacks = {0xB101: explain, 0xB102: capture}
        registrations = [(0xB101, "E"), (0xB102, "S")]
        if pause_game is not None:
            self.callbacks[0xB103] = pause_game
            registrations.append((0xB103, "P"))
        self.registered = []
        if sys.platform == "win32":
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            for identifier, letter in registrations:
                # MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
                if user32.RegisterHotKey(None, identifier, 0x4003, ord(letter)):
                    self.registered.append(identifier)
            app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == 0x0312 and msg.wParam in self.callbacks:
                self.callbacks[msg.wParam]()
                return True, 0
        return False, 0

    def close(self):
        if sys.platform == "win32":
            for identifier in self.registered:
                ctypes.windll.user32.UnregisterHotKey(None, identifier)
            self.app.removeNativeEventFilter(self)
