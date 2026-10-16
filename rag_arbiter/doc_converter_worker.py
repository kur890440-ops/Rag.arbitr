"""Private worker: an owned Word instance, macros off, no updates, read-only input."""
import json
import sys
from pathlib import Path
from uuid import uuid4


def main():
    import pythoncom
    import win32com.client
    import win32process
    import psutil
    source,target,owner=map(Path,sys.argv[1:])
    existing=set()
    for proc in psutil.process_iter(['pid','name','create_time']):
        if (proc.info['name'] or '').lower()=='winword.exe':
            existing.add((proc.pid,proc.info['create_time']))
    pythoncom.CoInitialize()
    word=None;document=None;owned=False;options={};blank=None
    try:
        word=win32com.client.DispatchEx('Word.Application')
        word.Visible=False;word.DisplayAlerts=0;word.AutomationSecurity=3
        blank=word.Documents.Add()
        _,pid=win32process.GetWindowThreadProcessId(blank.ActiveWindow.Hwnd)
        created=psutil.Process(pid).create_time()
        if (pid,created) in existing:
            raise RuntimeError('Refusing an existing Word process')
        owner.write_text(json.dumps(dict(pid=pid,created=created)),encoding='utf-8');owned=True
        blank.Close(SaveChanges=0);blank=None
        for name in ('UpdateLinksAtOpen','SaveNormalPrompt'):
            options[name]=getattr(word.Options,name)
            setattr(word.Options,name,False)
        # Word 2013 rejects passwords longer than 15 characters.
        password=uuid4().hex[:15]
        document=word.Documents.Open(FileName=str(source),ConfirmConversions=False,ReadOnly=True,
            AddToRecentFiles=False,PasswordDocument=password,PasswordTemplate=password,
            WritePasswordDocument=password,WritePasswordTemplate=password,Visible=False,
            OpenAndRepair=False,NoEncodingDialog=True)
        document.SaveAs2(FileName=str(target),FileFormat=12,AddToRecentFiles=False)
    finally:
        if blank is not None:blank.Close(SaveChanges=0)
        if owned:
            if document is not None:document.Close(SaveChanges=0)
            for name,value in options.items():setattr(word.Options,name,value)
            word.Quit(SaveChanges=0)
        document=None;word=None
        pythoncom.CoUninitialize()

if __name__=='__main__':main()

