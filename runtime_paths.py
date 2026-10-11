"""Stable portable data paths and bundled resource/launcher locations."""
from pathlib import Path
import os
import subprocess
import sys
import threading

VERSION = '0.5.0'


def locations(module_file=None, executable=None, frozen=None):
    resource_root = Path(module_file or __file__).resolve().parent
    bundled = getattr(sys, 'frozen', False) if frozen is None else frozen
    executable = Path(executable or sys.executable).resolve()
    return (executable.parent if bundled else resource_root), resource_root


ROOT, RESOURCES = locations()
ICON = RESOURCES / 'app.ico'
_process_lock = threading.Lock()


def launcher(folder=None):
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve()
    return Path(folder or ROOT) / 'AIUsage.exe'


def launch_command(folder=None):
    executable = launcher(folder)
    if executable.exists():
        return subprocess.list2cmdline([str(executable)])
    return subprocess.list2cmdline([sys.executable, str(Path(folder or ROOT) / 'app.py')])


def taskbar_icon(folder=None):
    # Explorer must retain a stable icon after a one-file extraction is removed.
    executable = launcher(folder)
    return str(executable if executable.exists() else ICON) + ',0'


def external_process(arguments, **kwargs):
    """Do not pass the bundled DLL search path to an installed Codex process."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return subprocess.Popen(arguments, **kwargs)
    import ctypes
    setter = ctypes.WinDLL('kernel32', use_last_error=True).SetDllDirectoryW
    setter.argtypes = [ctypes.c_wchar_p]
    setter.restype = ctypes.c_bool
    env = dict(kwargs.pop('env', os.environ))
    bundle = str(RESOURCES).casefold().rstrip('\\/')
    env['PATH'] = os.pathsep.join(
        item for item in env.get('PATH', '').split(os.pathsep)
        if not (item.casefold().rstrip('\\/') == bundle
                or item.casefold().startswith(bundle + os.sep)))
    with _process_lock:
        if not setter(None):
            raise OSError(ctypes.get_last_error(), 'Cannot restore system DLL search path')
        try:
            return subprocess.Popen(arguments, env=env, **kwargs)
        finally:
            setter(str(RESOURCES))
