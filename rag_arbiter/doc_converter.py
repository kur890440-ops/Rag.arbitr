"""Bounded local DOC conversion with an isolated LibreOffice profile."""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from .documents import digest, file_hash, save_json


class DocConversionError(ValueError):
    pass


def validate_doc(path):
    import olefile
    with Path(path).open('rb') as stream:
        header=stream.read(8)
    if header.startswith(b'{\\rtf'):
        return  # Word commonly stores RTF under a .doc filename.
    if header != bytes.fromhex('D0CF11E0A1B11AE1'):
        raise DocConversionError('DOC_INVALID_CONTAINER')
    try:
        with olefile.OleFileIO(str(path)) as container:
            if not container.exists('WordDocument'):
                raise DocConversionError('DOC_INVALID_CONTAINER')
            with container.openstream('WordDocument') as stream:
                fib=stream.read(12)
            if len(fib)!=12 or fib[:2] != b'\xec\xa5':
                raise DocConversionError('DOC_INVALID_HEADER')
            if int.from_bytes(fib[10:12],'little') & 0x8100:
                raise DocConversionError('DOC_PASSWORD_PROTECTED')
    except DocConversionError:
        raise
    except (OSError,ValueError,IndexError) as exc:
        raise DocConversionError('DOC_INVALID_CONTAINER') from exc


def libreoffice_executable():
    override = os.environ.get('RAG_LIBREOFFICE_PATH')
    candidates = [Path(override)] if override else [
        Path(__file__).resolve().parents[1] / '.tools/libreoffice/program/soffice.com',
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'LibreOffice/program/soffice.com',
        Path(shutil.which('soffice') or shutil.which('libreoffice') or '__missing__')]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise DocConversionError('DOC_CONVERTER_UNAVAILABLE: LibreOffice not found')


def converter_identity():
    exe = libreoffice_executable()
    # Include the engine build and executable, not just the Python adapter version.
    version = exe.parent / 'version.ini'
    if not version.is_file():
        version = exe.parent / 'versionrc'
    return dict(adapter='libreoffice-docx-1', engine='LibreOffice',
                executable_hash=file_hash(exe), build_hash=file_hash(version) if version.is_file() else None)


def isolated_profile(work):
    profile = work / 'profile'
    (profile / 'user').mkdir(parents=True)
    (profile / 'user' / 'registrymodifications.xcu').write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
        '<item oor:path="/org.openoffice.Office.Writer/Content/Update">'
        '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>'
        '</oor:items>', encoding='utf-8')
    return profile.resolve().as_uri()


def stop_conversion(process):
    """Stop only this invocation and its children, never a user's office instance."""
    if process.poll() is not None:
        return
    import psutil
    children = []
    if getattr(process, 'pid', None):
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.Error:
            pass
    for child in reversed(children):
        try:
            child.kill()
        except psutil.Error:
            pass
    process.kill()
    process.wait(timeout=10)
    if children:
        psutil.wait_procs(children, timeout=5)


def convert_doc(path, config, reporter):
    validate_doc(path)
    identity = converter_identity()
    original_hash = file_hash(path)
    root = config.cache_path / 'doc-conversion' / digest([original_hash, identity])
    root.mkdir(parents=True, exist_ok=True)
    output = root / 'document.docx'
    manifest = root / 'conversion.json'
    from .docx_adapter import validate_docx
    if output.is_file() and manifest.is_file():
        try:
            cached = json.loads(manifest.read_text(encoding='utf-8'))
            if cached['output_hash'] == file_hash(output) and cached['source_hash'] == original_hash:
                validate_docx(output)
                return output, identity
        except (ValueError, KeyError, OSError):
            pass
    with tempfile.TemporaryDirectory(prefix='working-', dir=root) as work:
        work = Path(work).resolve()
        local, target = work / 'input.doc', work / 'input.docx'
        shutil.copyfile(path, local)
        if file_hash(local) != original_hash:
            raise DocConversionError('DOC_SOURCE_CHANGED')
        profile = isolated_profile(work)
        reporter.check()
        process = subprocess.Popen([str(libreoffice_executable()), '-env:UserInstallation=' + profile,
            '--headless', '--nologo', '--nodefault', '--norestore', '--convert-to',
            'docx:Office Open XML Text', '--outdir', str(work), str(local)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + config.doc_conversion_timeout
        try:
            while process.poll() is None:
                reporter.check()
                if time.monotonic() > deadline:
                    raise DocConversionError('DOC_CONVERSION_TIMEOUT')
                time.sleep(.1)
            reporter.check()
            if process.returncode or not target.is_file():
                raise DocConversionError('DOC_CONVERSION_FAILED')
            validate_docx(target)
            if file_hash(path) != original_hash:
                raise DocConversionError('DOC_SOURCE_CHANGED')
            target.replace(output)
            save_json(manifest, dict(source_hash=original_hash, output_hash=file_hash(output), identity=identity))
        finally:
            stop_conversion(process)
    return output, identity
