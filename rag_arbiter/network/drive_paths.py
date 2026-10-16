"""Resolve current-user drive mappings without mounting drives or storing credentials."""
import os
from pathlib import PureWindowsPath


def mapped_share(drive):
    import ctypes
    from ctypes import wintypes
    import winreg

    api = ctypes.WinDLL('mpr').WNetGetConnectionW
    api.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if api(drive, buffer, ctypes.byref(size)) == 0:
        return buffer.value
    # Disconnected persistent mappings can still be reached through their UNC path.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Network\\' + drive[0]) as key:
            value, kind = winreg.QueryValueEx(key, 'RemotePath')
            if kind == winreg.REG_SZ:
                return value
    except OSError:
        pass
    return None


def resolve_drive_path(value, lookup=None):
    if lookup is None:
        if os.name != 'nt':
            return value
        lookup = mapped_share
    path = PureWindowsPath(value)
    if len(path.drive) != 2 or path.drive[1] != ':' or not path.is_absolute():
        return value
    if '..' in path.parts:
        raise ValueError('Parent traversal is not allowed')
    share = lookup(path.drive)
    if not share:
        return value
    remote = PureWindowsPath(share)
    if not str(remote).startswith('\\\\') or not remote.is_absolute() or '..' in remote.parts:
        raise ValueError('Invalid network drive mapping')
    return str(remote.joinpath(*path.parts[1:]))
