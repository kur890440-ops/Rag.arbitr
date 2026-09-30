"""Native Ollama loopback adapter. No cloud endpoints, proxies, redirects or auto-pull."""
import base64
import io
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from PIL import Image
from .models import RuntimeResponse


class RuntimeUnavailable(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeUnavailable("Runtime redirect refused: recognition must stay on loopback")


def gpu_diagnostics():
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"nvidia_detected": result.returncode == 0, "nvidia_smi": result.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return {"nvidia_detected": False, "nvidia_smi": "unavailable"}


class OllamaRuntime:
    def __init__(self, settings):
        self.settings = settings
        url = urllib.parse.urlsplit(settings.endpoint)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError("Recognition endpoint must be an HTTP loopback address")
        host = "[::1]" if url.hostname == "::1" else "127.0.0.1"
        self.endpoint = f"http://{host}:{url.port or 11434}"
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.info = None
        self.loaded_by_us = False

    def request(self, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.endpoint + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.http.open(request, timeout=timeout or self.settings.timeout) as response:
                raw = response.read().decode("utf-8")
            return json.loads(raw), raw
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeUnavailable(f"Ollama {path}: HTTP {exc.code}: {details[:800]}. Check model, VRAM and runtime log.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeUnavailable(f"Ollama {path} unavailable/timed out at {self.endpoint}. Start native Ollama (ollama serve); install model with: ollama pull {self.settings.model}. Details: {exc}") from exc

    def options(self):
        options = {"temperature": self.settings.temperature, "seed": self.settings.seed,
                   "num_ctx": self.settings.context_tokens, "num_predict": self.settings.max_generation_tokens,
                   "repeat_penalty": self.settings.repeat_penalty, "repeat_last_n": self.settings.repeat_last_n}
        if self.settings.device == "cpu":
            options["num_gpu"] = 0
        elif self.settings.device == "cuda":
            options["num_gpu"] = -1
        return options

    def placement(self):
        state, _ = self.request("/api/ps", timeout=10)
        item = next((m for m in state.get("models", []) if m.get("name") == self.settings.model or m.get("model") == self.settings.model), None)
        if not item:
            return {"effective_device": "unknown", "size_vram": None}
        vram, size = item.get("size_vram", 0), item.get("size", 0)
        return {"effective_device": "cpu" if vram == 0 else "gpu" if vram >= size else "gpu+cpu",
                "size_vram": vram, "model_memory_bytes": size, "runtime_model": item.get("name")}

    def preflight(self):
        if self.info is not None:
            return self.info
        version, _ = self.request("/api/version", timeout=10)
        tags, _ = self.request("/api/tags", timeout=10)
        model = next((m for m in tags.get("models", []) if m.get("name") == self.settings.model), None)
        if not model:
            raise RuntimeUnavailable(f"Local model missing. Run: ollama pull {self.settings.model}; then recognition status")
        show, _ = self.request("/api/show", {"model": self.settings.model}, timeout=15)
        if show.get("remote_host") or show.get("remote_model") or "cloud" in self.settings.model.lower():
            raise RuntimeUnavailable("Cloud models are forbidden for document recognition")
        details = show.get("details", {})
        family = details.get("family", "")
        params = show.get("model_info", {}).get("general.parameter_count", 0)
        if "qwen3vl" not in family.replace("_", "").lower() or not 1_000_000_000 < params < 3_000_000_000:
            raise RuntimeUnavailable(f"Expected Qwen3-VL-2B, got family={family}, parameter_count={params}")
        if "vision" not in show.get("capabilities", []):
            raise RuntimeUnavailable("Selected model lacks vision capability")
        quantization = details.get("quantization_level", "unknown")
        if quantization != self.settings.quantization:
            raise RuntimeUnavailable(f"Model quantization {quantization} differs from config {self.settings.quantization}")
        # An empty generate loads the model without recognizing a document.
        self.request("/api/generate", {"model": self.settings.model, "stream": False,
                     "keep_alive": self.settings.keep_alive, "options": self.options()})
        self.loaded_by_us = True
        placement = self.placement()
        if self.settings.device == "cuda" and placement["effective_device"] not in {"gpu", "gpu+cpu"}:
            raise RuntimeUnavailable("CUDA requested but Ollama reports no GPU model allocation; inspect VRAM/runtime log")
        if placement["effective_device"] == "unknown":
            raise RuntimeUnavailable("Runtime loaded model but /api/ps cannot verify placement")
        self.info = {"runtime": "ollama", "runtime_version": version["version"], "model": self.settings.model,
                     "model_version": model["digest"], "quantization": quantization,
                     **placement, **gpu_diagnostics()}
        return self.info

    def generate(self, request, prompt, schema):
        self.preflight()
        with Image.open(request.image_path) as original:
            image = original.convert("RGB")
            try:
                image.thumbnail((self.settings.max_image_resolution,) * 2, Image.Resampling.LANCZOS)
                with io.BytesIO() as stream:
                    image.save(stream, format="PNG")
                    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
                width, height = image.size
            finally:
                image.close()
        body = {"model": self.settings.model, "messages": [{"role": "user", "content": prompt, "images": [encoded]}],
                "format": schema, "stream": False, "think": False,
                "options": self.options(), "keep_alive": self.settings.keep_alive}
        response, raw = self.request("/api/chat", body)
        try:
            placement = self.placement()
        except RuntimeUnavailable as exc:
            placement = {"effective_device": "unknown", "placement_error": str(exc)}
        diagnostics = {**placement, "image_width": width, "image_height": height,
                       "configured_max_output": self.settings.max_generation_tokens,
                       "configured_context": self.settings.context_tokens,
                       **{k: response.get(k) for k in ("done", "done_reason", "total_duration", "load_duration", "eval_count", "prompt_eval_count")}}
        return RuntimeResponse(raw=raw, content=response.get("message", {}).get("content", ""),
                               device=placement["effective_device"], diagnostics=diagnostics)

    def close(self):
        if self.loaded_by_us:
            try:
                self.request("/api/generate", {"model": self.settings.model, "stream": False, "keep_alive": 0}, timeout=15)
            except RuntimeUnavailable as exc:
                print(f"Recognition model unload warning: {exc}")
            finally:
                self.loaded_by_us = False
                self.info = None
