"""Shared sampling for CLI and Web Day29 benchmark replay."""
import threading
import subprocess
import psutil

class Resources:
    """Sample whole-device VRAM and benchmark+Ollama RSS; not isolated model allocation."""
    def __enter__(self):
        self.stop = threading.Event(); self.samples = []
        def work():
            while not self.stop.is_set():
                rss = psutil.Process().memory_info().rss
                for p in psutil.process_iter(['name', 'memory_info']):
                    try:
                        if (p.info['name'] or '').lower() in ('ollama.exe', 'ollama_llama_server.exe'):
                            rss += p.info['memory_info'].rss
                    except psutil.Error: pass
                vram = None
                try:
                    output = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=5)
                    vram = int(output.splitlines()[0])
                except (OSError, ValueError, subprocess.SubprocessError): pass
                self.samples.append(dict(rss_bytes=rss, device_vram_mib=vram))
                self.stop.wait(1)
        self.thread = threading.Thread(target=work, daemon=True); self.thread.start()
        return self
    def __exit__(self, *args):
        self.stop.set(); self.thread.join(6)
    def summary(self):
        return dict(samples=len(self.samples), sample_interval_seconds=1,
            rss_peak_bytes=max((s['rss_bytes'] for s in self.samples), default=None),
            device_vram_peak_mib=max((s['device_vram_mib'] for s in self.samples if s['device_vram_mib'] is not None), default=None))
