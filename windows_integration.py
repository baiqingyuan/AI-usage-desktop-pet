"""Native taskbar identity, relaunch properties and a per-user single instance."""
import ctypes as ct
import hashlib
import os
from pathlib import Path
import uuid
import runtime_paths

APP_ID = 'Bai.AIUsageDesktop.Companion'


class GUID(ct.Structure):
    _fields_ = [('Data1', ct.c_uint32), ('Data2', ct.c_uint16), ('Data3', ct.c_uint16), ('Data4', ct.c_ubyte*8)]
    @classmethod
    def from_string(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class PROPERTYKEY(ct.Structure):
    _fields_ = [('fmtid', GUID), ('pid', ct.c_uint32)]


class PVData(ct.Union):
    _fields_ = [('pointer', ct.c_void_p), ('padding', ct.c_ubyte*16)]


class PROPVARIANT(ct.Structure):
    _fields_ = [('vt', ct.c_ushort), ('reserved1', ct.c_ushort),
                ('reserved2', ct.c_ushort), ('reserved3', ct.c_ushort), ('data', PVData)]


class NOTIFYICONDATA(ct.Structure):
    _fields_ = [('cbSize', ct.c_uint32), ('hWnd', ct.c_void_p), ('uID', ct.c_uint32),
                ('uFlags', ct.c_uint32), ('uCallbackMessage', ct.c_uint32), ('hIcon', ct.c_void_p),
                ('szTip', ct.c_wchar*128), ('dwState', ct.c_uint32), ('dwStateMask', ct.c_uint32),
                ('szInfo', ct.c_wchar*256), ('uTimeoutOrVersion', ct.c_uint32),
                ('szInfoTitle', ct.c_wchar*64), ('dwInfoFlags', ct.c_uint32),
                ('guidItem', GUID), ('hBalloonIcon', ct.c_void_p)]


def set_window_properties(hwnd, values):
    shell = ct.WinDLL('shell32')
    shell.SHGetPropertyStoreForWindow.argtypes = [ct.c_void_p, ct.POINTER(GUID), ct.POINTER(ct.c_void_p)]
    shell.SHGetPropertyStoreForWindow.restype = ct.c_long
    iid = GUID.from_string('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
    store = ct.c_void_p()
    hr = shell.SHGetPropertyStoreForWindow(hwnd, ct.byref(iid), ct.byref(store))
    if hr < 0:
        raise OSError('Cannot open taskbar property store')
    table = ct.cast(store, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
    set_value = ct.WINFUNCTYPE(ct.c_long, ct.c_void_p, ct.POINTER(PROPERTYKEY), ct.POINTER(PROPVARIANT))(table[6])
    commit = ct.WINFUNCTYPE(ct.c_long, ct.c_void_p)(table[7])
    release = ct.WINFUNCTYPE(ct.c_ulong, ct.c_void_p)(table[2])
    try:
        for pid, value in values.items():
            buffer = ct.create_unicode_buffer(value)
            pv = PROPVARIANT()
            pv.vt = 31  # VT_LPWSTR; SetValue copies the borrowed Python buffer.
            pv.data.pointer = ct.cast(buffer, ct.c_void_p)
            key = PROPERTYKEY(GUID.from_string('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'), pid)
            if set_value(store, ct.byref(key), ct.byref(pv)) < 0:
                raise OSError('Cannot set taskbar property')
        if commit(store) < 0:
            raise OSError('Cannot commit taskbar property')
    finally:
        release(store)


def set_shortcut_app_id(path):
    ole = ct.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ct.c_void_p, ct.c_uint]
    ole.CoInitializeEx.restype = ct.c_long
    hr = ole.CoInitializeEx(None, 2)
    if hr < 0 and hr != -2147417850:  # RPC_E_CHANGED_MODE: caller already initialized COM.
        raise OSError('Cannot initialize shortcut properties')
    try:
        _set_shortcut_app_id(path)
    finally:
        if hr >= 0:
            ole.CoUninitialize()


def _set_shortcut_app_id(path):
    shell = ct.WinDLL('shell32')
    shell.SHGetPropertyStoreFromParsingName.argtypes = [ct.c_wchar_p, ct.c_void_p, ct.c_uint,
                                                      ct.POINTER(GUID), ct.POINTER(ct.c_void_p)]
    shell.SHGetPropertyStoreFromParsingName.restype = ct.c_long
    iid = GUID.from_string('886d8eeb-8cf2-4446-8d02-cdba1dbdcf99')
    store = ct.c_void_p()
    hr = shell.SHGetPropertyStoreFromParsingName(str(path), None, 2, ct.byref(iid), ct.byref(store))
    if hr < 0:
        raise OSError('Cannot open shortcut property store: ' + hex(hr & 0xffffffff))
    table = ct.cast(store, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
    set_value = ct.WINFUNCTYPE(ct.c_long, ct.c_void_p, ct.POINTER(PROPERTYKEY), ct.POINTER(PROPVARIANT))(table[6])
    commit = ct.WINFUNCTYPE(ct.c_long, ct.c_void_p)(table[7])
    release = ct.WINFUNCTYPE(ct.c_ulong, ct.c_void_p)(table[2])
    try:
        buffer = ct.create_unicode_buffer(APP_ID)
        pv = PROPVARIANT()
        pv.vt = 31
        pv.data.pointer = ct.cast(buffer, ct.c_void_p)
        key = PROPERTYKEY(GUID.from_string('9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3'), 5)
        if set_value(store, ct.byref(key), ct.byref(pv)) < 0 or commit(store) < 0:
            raise OSError('Cannot set shortcut AppUserModelID')
    finally:
        release(store)


class WindowsBridge:
    def __init__(self, folder):
        self.root = None
        self.folder = Path(folder).resolve()
        self.enabled = os.name == 'nt'
        self.mutex = None
        self.event = None
        self.already_running = False
        self.tray_added = False
        self.pending = None
        if not self.enabled:
            return
        self.kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ct.c_void_p, ct.c_bool, ct.c_wchar_p]
        self.kernel.CreateMutexW.restype = ct.c_void_p
        self.kernel.CreateEventW.argtypes = [ct.c_void_p, ct.c_bool, ct.c_bool, ct.c_wchar_p]
        self.kernel.CreateEventW.restype = ct.c_void_p
        self.kernel.SetEvent.argtypes = [ct.c_void_p]
        self.kernel.WaitForSingleObject.argtypes = [ct.c_void_p, ct.c_uint]
        self.kernel.WaitForSingleObject.restype = ct.c_uint
        self.kernel.CloseHandle.argtypes = [ct.c_void_p]
        suffix = hashlib.sha256(str(self.folder).lower().encode()).hexdigest()[:20]
        self.mutex = self.kernel.CreateMutexW(None, False, 'Local\\AIUsage.'+suffix)
        self.already_running = ct.get_last_error() == 183
        if not self.mutex:
            raise OSError('Cannot create single-instance mutex')
        self.event = self.kernel.CreateEventW(None, False, False, 'Local\\AIUsage.Show.'+suffix)
        if not self.event:
            raise OSError('Cannot create activation event')
        shell = ct.WinDLL('shell32')
        shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ct.c_wchar_p]
        shell.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    def activate_existing(self):
        if self.already_running:
            self.kernel.SetEvent(self.event)
            self.close()
            return True
        return False

    def attach(self, root):
        self.root = root
        if not self.enabled:
            return
        root.update_idletasks()
        self.user = ct.WinDLL('user32', use_last_error=True)
        self.user.GetAncestor.argtypes = [ct.c_void_p, ct.c_uint]
        self.user.GetAncestor.restype = ct.c_void_p
        self.hwnd = self.user.GetAncestor(root.winfo_id(), 2)
        self.user.GetWindowLongPtrW.argtypes = [ct.c_void_p, ct.c_int]
        self.user.GetWindowLongPtrW.restype = ct.c_ssize_t
        self.user.SetWindowLongPtrW.argtypes = [ct.c_void_p, ct.c_int, ct.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = ct.c_ssize_t
        self.user.ShowWindow.argtypes = [ct.c_void_p, ct.c_int]
        self.user.SetForegroundWindow.argtypes = [ct.c_void_p]
        self.user.SetWindowPos.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_uint]
        # Keep the override-redirect surface in the taskbar, without a native caption.
        ex = self.user.GetWindowLongPtrW(self.hwnd, -20)
        self.user.SetWindowLongPtrW(self.hwnd, -20, (ex | 0x40000) & ~0x80)
        style = self.user.GetWindowLongPtrW(self.hwnd, -16)
        self.user.SetWindowLongPtrW(self.hwnd, -16, (style | 0x80000 | 0x20000) & ~0xC00000)
        set_window_properties(self.hwnd, {2: runtime_paths.launch_command(self.folder),
                                         3: runtime_paths.taskbar_icon(self.folder),
                                         4: 'AI 用量挂件', 5: APP_ID})
        self.user.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x27)
        # A hide/show cycle lets Explorer discover the changed taskbar eligibility.
        self.user.ShowWindow(self.hwnd, 0)
        self.user.ShowWindow(self.hwnd, 5)
        self.apply_topmost()
        self.add_tray()

    def add_tray(self):
        self.user.LoadImageW.argtypes = [ct.c_void_p, ct.c_wchar_p, ct.c_uint, ct.c_int, ct.c_int, ct.c_uint]
        self.user.LoadImageW.restype = ct.c_void_p
        self.user.DestroyIcon.argtypes = [ct.c_void_p]
        self.icon_handle = self.user.LoadImageW(None, str(runtime_paths.ICON), 1, 32, 32, 0x10)
        if not self.icon_handle:
            return
        self.user.CallWindowProcW.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_size_t, ct.c_ssize_t]
        self.user.CallWindowProcW.restype = ct.c_ssize_t
        callback_message = 0x8049
        signature = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_void_p, ct.c_uint, ct.c_size_t, ct.c_ssize_t)
        def procedure(hwnd, message, wparam, lparam):
            if message == callback_message:
                if lparam in (0x202, 0x203):
                    self.pending = 'restore'
                elif lparam == 0x205:
                    self.pending = 'menu'
                return 0
            return self.user.CallWindowProcW(self.old_proc, hwnd, message, wparam, lparam)
        self.callback = signature(procedure)
        self.old_proc = self.user.SetWindowLongPtrW(self.hwnd, -4, ct.cast(self.callback, ct.c_void_p).value)
        self.notify = NOTIFYICONDATA()
        self.notify.cbSize = ct.sizeof(self.notify)
        self.notify.hWnd = self.hwnd
        self.notify.uID = 1
        self.notify.uFlags = 7
        self.notify.uCallbackMessage = callback_message
        self.notify.hIcon = self.icon_handle
        self.notify.szTip = 'AI 用量挂件 · 点击唤回，右键打开菜单'
        self.shell = ct.WinDLL('shell32')
        self.shell.Shell_NotifyIconW.argtypes = [ct.c_uint, ct.POINTER(NOTIFYICONDATA)]
        self.shell.Shell_NotifyIconW.restype = ct.c_bool
        self.tray_added = bool(self.shell.Shell_NotifyIconW(0, ct.byref(self.notify)))

    def poll(self):
        if self.enabled and self.root and self.root.winfo_exists():
            desired = bool(self.root.attributes('-topmost'))
            actual = bool(self.user.GetWindowLongPtrW(self.hwnd, -20) & 0x8)
            popup = getattr(self.root.companion_surface, 'popup_window', None)
            if desired != actual and not (popup and not popup.closed):
                self.apply_topmost()
        if self.enabled and self.event and self.kernel.WaitForSingleObject(self.event, 0) == 0:
            self.restore()
        if self.pending:
            action = self.pending
            self.pending = None
            self.restore()
            if action == 'menu':
                self.root.after(50, self.root.companion_surface.menu)

    def restore(self):
        surface = getattr(self.root, 'companion_surface', None)
        popup = getattr(surface, 'popup_window', None)
        if popup and not popup.closed:
            popup.lift()
            popup.native_topmost()
            popup.focus_force()
            return
        panel = surface.app.active_panel() if surface and hasattr(surface.app, 'active_panel') else None
        if panel is not None:
            panel.deiconify()
            panel.lift()
            panel.focus_set()
            if self.enabled:
                hwnd = self.user.GetAncestor(panel.winfo_id(), 2)
                self.user.ShowWindow(hwnd, 9)
                self.user.SetForegroundWindow(hwnd)
            return
        if surface:
            surface.render()
        self.root.deiconify()
        if self.enabled:
            self.user.ShowWindow(self.hwnd, 9)
            self.apply_topmost()
            self.user.SetForegroundWindow(self.hwnd)
        self.root.lift()

    def apply_topmost(self):
        if self.enabled:
            level = -1 if self.root.attributes('-topmost') else -2
            self.user.SetWindowPos(self.hwnd, level, 0, 0, 0, 0, 0x13)

    def minimize(self):
        if self.enabled:
            self.user.ShowWindow(self.hwnd, 6)

    def close(self):
        if self.enabled:
            if self.tray_added:
                self.shell.Shell_NotifyIconW(2, ct.byref(self.notify))
                self.tray_added = False
            if hasattr(self, 'old_proc'):
                self.user.IsWindow.argtypes = [ct.c_void_p]
                if self.user.IsWindow(self.hwnd):
                    self.user.SetWindowLongPtrW(self.hwnd, -4, self.old_proc)
                del self.old_proc
            if getattr(self, 'icon_handle', None):
                self.user.DestroyIcon(self.icon_handle)
                self.icon_handle = None
            for handle in (self.event, self.mutex):
                if handle:
                    self.kernel.CloseHandle(handle)
            self.event = self.mutex = None
