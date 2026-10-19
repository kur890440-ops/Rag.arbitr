"""Windows handle-based no-overwrite publication, separate from read-only MCP."""
import contextlib,ctypes,os,time,json
from pathlib import Path,PureWindowsPath
import portalocker
from fastapi import HTTPException
from .document_workspace import sha,uid
from . import workspace_formats as formats
from .document_diff import structured_diff
from ..network.connector import no_links,private_path,authorized_root

@contextlib.contextmanager
def locked_handle(path,write=False,directory=False):
    if os.name!='nt':raise ValueError('PUBLICATION_PLATFORM_UNVERIFIED')
    import win32file,win32con
    access=(0 if directory else win32con.GENERIC_READ) | (win32con.DELETE if write else 0)
    flags=win32con.FILE_FLAG_BACKUP_SEMANTICS if directory else 0
    h=win32file.CreateFile(str(path),access,win32con.FILE_SHARE_READ,None,win32con.OPEN_EXISTING,flags|win32file.FILE_FLAG_OPEN_REPARSE_POINT,None)
    try:
        no_links(path)
        # Verify the opened object, not only the path checked before opening.
        from ctypes import wintypes
        fn=ctypes.windll.kernel32.GetFinalPathNameByHandleW
        fn.argtypes=[wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD];fn.restype=wintypes.DWORD
        out=ctypes.create_unicode_buffer(32768);n=fn(int(h),out,len(out),0)
        value=out.value
        if value.startswith('\\\\?\\UNC\\'):value='\\\\'+value[8:]
        elif value.startswith('\\\\?\\'):value=value[4:]
        if not n or PureWindowsPath(value)!=PureWindowsPath(path.absolute()):raise ValueError('HANDLE_PATH_MISMATCH')
        yield h
    finally:h.Close()

def read_handle(handle,limit=20*1024*1024):
    import win32file
    size=win32file.GetFileSize(handle)
    if size>limit:raise ValueError('FILE_TOO_LARGE')
    win32file.SetFilePointer(handle,0,0)
    return win32file.ReadFile(handle,size)[1] if size else b''

def rename_handle(handle,target):
    from ctypes import wintypes
    class Rename(ctypes.Structure):
        _fields_=[('ReplaceIfExists',ctypes.c_ubyte),('RootDirectory',wintypes.HANDLE),('FileNameLength',wintypes.DWORD),('FileName',wintypes.WCHAR*1)]
    name=str(target.absolute()).encode('utf-16-le');buf=ctypes.create_string_buffer(Rename.FileName.offset+len(name)+2);info=ctypes.cast(buf,ctypes.POINTER(Rename)).contents
    info.ReplaceIfExists=0;info.RootDirectory=None;info.FileNameLength=len(name)
    ctypes.memmove(ctypes.addressof(buf)+Rename.FileName.offset,name,len(name))
    fn=ctypes.windll.kernel32.SetFileInformationByHandle
    fn.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];fn.restype=wintypes.BOOL
    if not fn(int(handle),3,buf,len(buf)):raise ctypes.WinError()

class ArchivePublisher:
    def __init__(self,workspace):self.ws=workspace;self.runs=workspace.runs

    def target(self,user,source_id,relative):
        self.ws.auth.require_admin(self.ws.actor(user))
        with self.runs.db() as db:source=db.get('network_sources',source_id)
        if not source or not source.get('enabled'):raise ValueError('SOURCE_NOT_ALLOWED')
        root=authorized_root(source['root_path'],self.runs.config.network_sources.allowed_roots)
        win=PureWindowsPath(relative)
        if not relative or win.root or win.drive or '..' in win.parts or ':' in relative or private_path(relative):raise ValueError('INVALID_TARGET_PATH')
        if any(p.endswith((' ','.')) or p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]} for p in win.parts):raise ValueError('INVALID_TARGET_PATH')
        target=root/relative.replace('\\','/');no_links(target.parent)
        if target.exists():no_links(target)
        allowed=[Path(p).absolute() for p in self.runs.config.workspace.publication_roots]
        if not target.resolve().is_relative_to(root.resolve()) or not any(target.resolve().is_relative_to(p.resolve()) for p in allowed):raise ValueError('PUBLICATION_ROOT_NOT_ALLOWED')
        for p in allowed:
            if target.resolve().is_relative_to(p.resolve()):no_links(p)
        return target

    @contextlib.contextmanager
    def parents(self,path):
        with contextlib.ExitStack() as stack:
            for folder in reversed(path.parent.parents):stack.enter_context(locked_handle(folder,directory=True))
            stack.enter_context(locked_handle(path.parent,directory=True))
            yield

    def probe(self,user,source_id,relative):
        target=self.target(user,source_id,relative);probe=target.parent/('.workspace-probe-'+uid()+'.tmp');renamed=probe.with_suffix('.checked')
        try:
            with self.parents(target):
                with probe.open('xb') as f:f.write(b'workspace-write-probe');f.flush();os.fsync(f.fileno())
                with locked_handle(probe,write=True) as h:rename_handle(h,renamed)
                if renamed.read_bytes()!=b'workspace-write-probe':raise ValueError('WRITE_PROBE_FAILED')
            return dict(write=True,handle_rename=True,atomicity='NOT_CLAIMED',checked_at=time.time())
        finally:
            for path in (probe,renamed):
                if path.exists():path.unlink()

    def logical_version(self,source_id,relative,content_hash):
        with self.runs.db() as db:
            matches=[v for v in db.all('document_versions') if v.get('source_id')==source_id and v.get('relative_path','').replace('\\','/')==relative.replace('\\','/') and v.get('content_hash')==content_hash]
        if len(matches)!=1:raise ValueError('LOGICAL_VERSION_NOT_AVAILABLE')
        return matches[0]['version_id']

    def prepare(self,user,request):
        row,data=self.ws.file_bytes(user,request.file_id)
        target=self.target(user,request.source_id,request.target_path)
        if target.suffix.lower()!='.'+row['format']:raise ValueError('FORMAT_MISMATCH')
        from .uploads import TYPES
        if target.suffix.lower() not in TYPES:raise ValueError('PUBLICATION_FORMAT_NOT_INDEXABLE')
        validation=self.ws.validate(user,row['id'])
        old=None
        with self.parents(target):
            if target.exists():
                if request.publication_operation=='CREATE_NEW':raise ValueError('CONFLICT')
                with locked_handle(target,write=True) as h:old=read_handle(h,self.ws.cfg.max_file_bytes)
            elif request.publication_operation=='REPLACE_EXISTING':raise ValueError('TARGET_MISSING')
        if old is not None and len(old)>self.ws.cfg.max_file_bytes:raise ValueError('FILE_TOO_LARGE')
        probe=self.probe(user,request.source_id,request.target_path)
        with self.runs.db() as db:versions=[f for f in db.all('network_files') if f.get('source_id')==request.source_id and f.get('relative_path','').replace('\\','/')==request.target_path.replace('\\','/')]
        previous=versions[0] if versions else {}
        if old is not None and previous.get('content_hash')!=sha(old):raise ValueError('ORIGINAL_VERSION_NOT_INDEXED')
        pid=uid();changes=structured_diff(row['id'],sha(old) if old else 'ABSENT',row['sha256'],formats.blocks(old,row['format']) if old else {'blocks':[]},formats.blocks(data,row['format']))
        diff=self.ws.put('workspace_diffs',dict(id=pid,owner_id=user['id'],old_hash=sha(old) if old else None,new_hash=row['sha256'],changes=changes))
        proposal=dict(id=pid,owner_id=user['id'],task_id=row['task_id'],file_id=row['id'],draft_sha256=row['sha256'],source_id=request.source_id,target_path=request.target_path,operation=request.publication_operation,previous_hash=sha(old) if old else None,previous_version=self.logical_version(request.source_id,request.target_path,sha(old)) if old is not None else None,diff_revision=pid,validation_id=validation['id'],requires_limitations_acceptance=bool(row['limitations'] or row['status']!='CONFIRMED'),limitations=row['limitations'],created_at=time.time(),expires_at=time.time()+self.ws.cfg.approval_ttl,capability=probe)
        with self.runs.db() as db:
            # Immutable proposal; a separate journal carries mutable publication state.
            db.db.execute("INSERT INTO workspace_proposals(id,data) VALUES (?,?)",(pid,json.dumps(proposal,ensure_ascii=False)));db.db.commit()
        return proposal

    def confirm(self,user,proposal_id,key,accept_limitations=False):
        if not key or len(key)>100:raise ValueError('IDEMPOTENCY_KEY_REQUIRED')
        proposal=self.ws.get('workspace_proposals',proposal_id,user)
        self.ws.auth.require_admin(self.ws.actor(user))
        locks=self.ws.root/'locks';locks.mkdir(exist_ok=True)
        with portalocker.Lock(str(locks/(proposal_id+'.lock')),timeout=2):
            with self.runs.db() as db:prior=db.get('workspace_publications',proposal_id)
            if prior:
                if prior['idempotency_key']!=key:raise ValueError('APPROVAL_ALREADY_CONSUMED')
                if prior['status'] in ('PUBLISHED_PENDING_INDEX','PROCESSING','INDEXED','INDEX_FAILED'):return self.status(user,proposal_id)
                # Reconcile a durable journal; never repeat the publication write.
                if prior['status']=='PUBLISHING':return self.recover(user,prior)
                return prior
            if time.time()>proposal['expires_at']:raise ValueError('APPROVAL_EXPIRED')
            if proposal['requires_limitations_acceptance'] and not accept_limitations:raise ValueError('LIMITATIONS_ACCEPTANCE_REQUIRED')
            file,data=self.ws.file_bytes(user,proposal['file_id'])
            if file['sha256']!=proposal['draft_sha256']:raise ValueError('DRAFT_CHANGED')
            self.ws.validate(user,file['id'])
            target=self.target(user,proposal['source_id'],proposal['target_path'])
            journal=dict(**proposal,idempotency_key=key,status='PUBLISHING',accepted_limitations=accept_limitations,confirmed_at=time.time(),new_hash=file['sha256'],error=None)
            stage=target.parent/('.workspace-stage-'+proposal_id+'.tmp');backup=target.parent/('.workspace-old-'+proposal_id+'.bak')
            journal.update(stage_path=str(stage),backup_path=str(backup))
            self.ws.put('workspace_publications',journal)
            try:
                with self.parents(target),contextlib.ExitStack() as stack:
                    oldhandle=None
                    if proposal['operation']=='REPLACE_EXISTING':
                        if not target.exists():raise ValueError('CONFLICT')
                        oldhandle=stack.enter_context(locked_handle(target,write=True))
                        if sha(read_handle(oldhandle,self.ws.cfg.max_file_bytes))!=proposal['previous_hash']:raise ValueError('CONFLICT')
                        if self.logical_version(proposal['source_id'],proposal['target_path'],proposal['previous_hash'])!=proposal['previous_version']:raise ValueError('CONFLICT')
                    elif target.exists():raise ValueError('CONFLICT')
                    with stage.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
                    staged=stack.enter_context(locked_handle(stage,write=True))
                    if sha(read_handle(staged,self.ws.cfg.max_file_bytes))!=file['sha256']:raise ValueError('STAGE_HASH_MISMATCH')
                    self.ws.auth.require_admin(self.ws.actor(user));self.target(user,proposal['source_id'],proposal['target_path'])
                    if oldhandle:rename_handle(oldhandle,backup)
                    try:rename_handle(staged,target)
                    except Exception:
                        if oldhandle:
                            try:rename_handle(oldhandle,target)
                            except Exception:journal['recovery_required']=True
                        raise
                    if sha(read_handle(staged,self.ws.cfg.max_file_bytes))!=file['sha256']:raise ValueError('PUBLISHED_HASH_MISMATCH')
                journal['status']='PUBLISHED_PENDING_INDEX';journal['published_at']=time.time()
                self.ws.put('workspace_publications',journal)
                try:
                    if self.ws.sync is None:raise ValueError('SYNC_UNAVAILABLE')
                    journal['scan_id']=self.ws.sync.start(proposal['source_id'])
                except Exception:journal['sync_error']='SYNC_START_FAILED'
            except Exception as exc:
                journal['status']='CONFLICT' if isinstance(exc,ValueError) and str(exc)=='CONFLICT' else 'FAILED'
                journal['error']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            self.ws.put('workspace_publications',journal)
            return journal

    def recover(self,user,journal):
        """Reconcile an interrupted confirmed operation without repeating a publish.

        Matching target means the previous rename completed. An absent target may
        be restored only from its verified original backup. Ambiguity fails closed.
        """
        target=self.target(user,journal['source_id'],journal['target_path'])
        backup=target.parent/('.workspace-old-'+journal['id']+'.bak')
        with self.parents(target):
            if target.exists():
                with locked_handle(target) as h:actual=sha(read_handle(h,self.ws.cfg.max_file_bytes))
                if actual==journal['new_hash']:
                    journal.update(status='PUBLISHED_PENDING_INDEX',recovery='COMPLETED_WRITE_RECONCILED',recovered_at=time.time())
                elif actual==journal['previous_hash']:
                    journal.update(status='FAILED',recovery='ORIGINAL_PRESENT',error='INTERRUPTED_BEFORE_PUBLISH')
                else:journal.update(status='CONFLICT',recovery='CONCURRENT_TARGET_PRESERVED',error='CONFLICT')
            elif journal['operation']=='REPLACE_EXISTING' and backup.exists():
                with locked_handle(backup,write=True) as h:
                    if sha(read_handle(h,self.ws.cfg.max_file_bytes))!=journal['previous_hash']:raise ValueError('RECOVERY_BACKUP_HASH_MISMATCH')
                    rename_handle(h,target)
                journal.update(status='FAILED',recovery='ORIGINAL_RESTORED',error='INTERRUPTED_REPLACEMENT')
            else:journal.update(status='FAILED',recovery='NO_PUBLISHED_TARGET',error='INTERRUPTED_BEFORE_PUBLISH')
        self.ws.put('workspace_publications',journal)
        if journal['status']=='PUBLISHED_PENDING_INDEX':
            try:
                if self.ws.sync is None:raise ValueError('SYNC_UNAVAILABLE')
                journal['scan_id']=self.ws.sync.start(journal['source_id'])
            except Exception:journal['sync_error']='SYNC_START_FAILED'
            self.ws.put('workspace_publications',journal)
        return journal

    def status(self,user,key):
        row=self.ws.get('workspace_publications',key,user)
        if row['status'] not in ('PUBLISHED_PENDING_INDEX','PROCESSING','INDEXED','INDEX_FAILED'):return row
        with self.runs.db() as db:files=db.all('network_files')
        current=next((f for f in files if f.get('source_id')==row['source_id'] and f.get('relative_path','').replace('\\','/')==row['target_path'].replace('\\','/') and f.get('content_hash')==row['new_hash']),None)
        if current:
            state=current.get('indexing_status')
            row['status']='INDEXED' if state=='SUCCESS' and current.get('ingestion_status')=='SUCCESS' else 'INDEX_FAILED' if state=='FAILED' or current.get('ingestion_status') in ('FAILED','CANCELLED') else 'PROCESSING'
            row['registry_file_id']=current['id']
            try:row['new_version']=self.logical_version(row['source_id'],row['target_path'],row['new_hash'])
            except ValueError:row['new_version']=None
            self.ws.put('workspace_publications',row)
        return row

