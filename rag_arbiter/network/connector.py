"""Read-only OS filesystem capability. No SMB credentials or LLM filesystem tools."""
import base64
import fnmatch
import os
import stat
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from ..application.uploads import TYPES


class SourceError(ValueError):
    pass


def error_code(exc):
    if isinstance(exc, SourceError):
        return str(exc)
    if isinstance(exc, PermissionError):
        return 'ACCESS_DENIED'
    if isinstance(exc, FileNotFoundError):
        return 'PATH_NOT_FOUND'
    return 'NETWORK_UNAVAILABLE' if isinstance(exc, OSError) else 'READ_ERROR'


def private_path(relative):
    """Service metadata and credential stores are never document sources."""
    for part in PureWindowsPath(relative).parts:
        name=part.casefold()
        if name in {'.git','.svn','.hg','.aws','.ssh','__pycache__','credentials','secrets'} or name=='.env' or name.startswith('.env.') or Path(name).stem in {'credentials','secrets'}:
            return True
    return False


def no_links(path):
    for item in [path, *path.parents]:
        st = item.lstat()
        if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
            raise SourceError('ACCESS_DENIED')


def authorized_root(value, allowed):
    path = Path(os.path.abspath(value))
    if not any(os.path.normcase(str(path)) == os.path.normcase(os.path.abspath(r)) for r in allowed):
        raise SourceError('ACCESS_DENIED')
    no_links(path)
    if not path.is_dir():
        raise SourceError('PATH_NOT_FOUND')
    return path


class Connector:
    def __init__(self, settings, sources):
        self.settings, self.sources = settings, sources

    def path(self, source_id, relative=''):
        source = self.sources(source_id)
        root = authorized_root(source['root_path'], self.settings.allowed_roots)
        if private_path(relative):raise SourceError('ACCESS_DENIED')
        win = PureWindowsPath(relative)
        if win.drive or win.root or Path(relative).is_absolute() or '..' in win.parts or ':' in relative:
            raise SourceError('ACCESS_DENIED')
        path = root / relative.replace('\\', '/')
        no_links(path)
        if not path.resolve().is_relative_to(root.resolve()):
            raise SourceError('ACCESS_DENIED')
        return path

    def metadata(self, source_id, relative):
        path = self.path(source_id, relative)
        st = path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise SourceError('ACCESS_DENIED')
        return dict(relative_path=relative, size=st.st_size, mtime_ns=st.st_mtime_ns,
                    modified_at=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat())

    def read(self, source_id, relative, offset=0, length=262144):
        if offset < 0 or not 1 <= length <= 262144:
            raise SourceError('READ_ERROR')
        path = self.path(source_id, relative)
        if path.suffix.lower() not in TYPES:
            raise SourceError('UNSUPPORTED_FORMAT')
        before = self.metadata(source_id, relative)
        if before['size'] > self.settings.max_file_size:
            raise SourceError('FILE_TOO_LARGE')
        # Validate the opened object, including the final Windows handle path,
        # before reading bytes (a path-only check would permit a rename race).
        with path.open('rb') as handle:
            if os.name == 'nt':
                import ctypes
                import msvcrt
                from ctypes import wintypes
                fn = ctypes.windll.kernel32.GetFinalPathNameByHandleW
                fn.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
                fn.restype = wintypes.DWORD
                buf = ctypes.create_unicode_buffer(32768)
                size = fn(msvcrt.get_osfhandle(handle.fileno()), buf, len(buf), 0)
                if not size or size >= len(buf):
                    raise SourceError('ACCESS_DENIED')
                final = buf.value
                if final.startswith('\\\\?\\UNC\\'):
                    final = '\\\\' + final[8:]
                elif final.startswith('\\\\?\\'):
                    final = final[4:]
                if os.path.normcase(final) != os.path.normcase(str(path.absolute())):
                    raise SourceError('ACCESS_DENIED')
            st = os.fstat(handle.fileno())
            if (st.st_size, st.st_mtime_ns) != (before['size'], before['mtime_ns']):
                raise SourceError('FILE_UNSTABLE')
            self.path(source_id, relative)
            if not os.path.samestat(st, path.stat()):
                raise SourceError('FILE_UNSTABLE')
            handle.seek(offset)
            data = handle.read(length)
            if self.metadata(source_id, relative) != before:
                raise SourceError('FILE_UNSTABLE')
        return dict(data=base64.b64encode(data).decode('ascii'), eof=offset+len(data)>=before['size'], **before)

    def directory(self, source_id, relative='', offset=0, limit=100):
        if not 0 <= offset <= self.settings.max_files_per_scan or not 1 <= limit <= 100:
            raise SourceError('READ_ERROR')
        rows = []
        with os.scandir(self.path(source_id, relative)) as entries:
            for i, entry in enumerate(entries):
                if i < offset:
                    continue
                if len(rows) == limit:
                    return dict(entries=rows, next_offset=i)
                rel = (Path(relative) / entry.name).as_posix()
                try:
                    path = self.path(source_id, rel)
                    rows.append(dict(name=entry.name, relative_path=rel, directory=path.is_dir()))
                except (OSError, ValueError):
                    rows.append(dict(name=entry.name, relative_path=rel, error='ACCESS_DENIED'))
        return dict(entries=rows, next_offset=None)

    def scan(self, source_id):
        source = self.sources(source_id)
        root = self.path(source_id)
        budget = self.settings.max_files_per_scan
        visited = 0
        def walk(folder, depth):
            nonlocal visited
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > budget:
                            raise SourceError('SCAN_LIMIT')
                        rel = Path(entry.path).relative_to(root).as_posix()
                        if entry.name.startswith(('.', '~$')) or entry.name.lower().endswith(('.tmp', '.part', '.lock')):
                            continue
                        if private_path(rel):continue
                        if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(entry.name, p) for p in source['exclude_patterns']):
                            continue
                        try:
                            path = self.path(source_id, rel)
                            if path.is_dir():
                                if source['recursive']:
                                    if depth >= self.settings.max_scan_depth:
                                        yield dict(relative_path=rel, error='DEPTH_LIMIT')
                                    else:
                                        yield from walk(path, depth+1)
                            elif path.suffix.lower() in {*TYPES, '.docx'} and any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(entry.name, p) for p in source['include_patterns']):
                                row = self.metadata(source_id, rel)
                                if path.suffix.lower() not in TYPES:
                                    row['error'] = 'UNSUPPORTED_FORMAT'
                                elif row['size'] > self.settings.max_file_size:
                                    row['error'] = 'FILE_TOO_LARGE'
                                yield row
                        except (OSError, ValueError) as exc:
                            if str(exc) == 'SCAN_LIMIT':
                                raise
                            yield dict(relative_path=rel, error=error_code(exc))
            except OSError as exc:
                yield dict(relative_path=Path(folder).relative_to(root).as_posix(), error=error_code(exc))
        yield from walk(root, 0)
