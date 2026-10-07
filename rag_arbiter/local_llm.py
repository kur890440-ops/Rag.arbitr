"""Text-only Ollama adapter implementing the existing LLMProvider contract."""
import time
from contextlib import nullcontext
from types import SimpleNamespace
from .llm import LLMResult
from .recognition.runtime import OllamaRuntime, RuntimeUnavailable


class LocalLLMProvider:
    def __init__(self, config, transport=None, operation_lock=None, ocr_model=None):
        self.config = config.model_copy(deep=True)
        self.runtime = OllamaRuntime(SimpleNamespace(endpoint=config.base_url, timeout=config.timeout))
        self.transport = transport or self.runtime.request
        self.operation_lock = operation_lock or nullcontext
        self.ocr_model = ocr_model

    def inspect(self):
        cfg = self.config
        if not cfg.enabled:
            raise RuntimeUnavailable('DISABLED')
        tags, _ = self.transport('/api/tags', timeout=10)
        model = next((m for m in tags.get('models', []) if m.get('name') == cfg.model), None)
        if model is None:
            raise RuntimeUnavailable('MODEL_NOT_INSTALLED')
        show, _ = self.transport('/api/show', {'model': cfg.model}, timeout=15)
        if (show.get('remote_host') or show.get('remote_model') or 'cloud' in cfg.model.lower()
                or 'vision' in show.get('capabilities', [])
                or 'completion' not in show.get('capabilities', [])):
            raise RuntimeUnavailable('TEXT_LOCAL_MODEL_REQUIRED')
        windows = [v for k, v in show.get('model_info', {}).items()
                   if k.endswith('.context_length') and isinstance(v, int)]
        if not windows or cfg.context_window > max(windows):
            raise RuntimeUnavailable('MODEL_CONTEXT_LIMIT')
        return dict(model=cfg.model, digest=model.get('digest'), details=show.get('details', {}),
                    model_context_window=max(windows), effective_context_window=cfg.context_window)

    def generate(self, request):
        cfg = self.config
        started = time.perf_counter()
        result = LLMResult(provider='local', model=cfg.model)
        try:
            result.diagnostics = self.inspect()
            messages = request.messages()
            prompt_bytes = sum(len(m['content'].encode('utf-8')) for m in messages)
            if prompt_bytes + 512 + cfg.max_output_tokens > cfg.context_window:
                result.error = {'code': 'LOCAL_CONTEXT_LIMIT'}
                return result
            payload = dict(model=cfg.model, messages=messages, stream=False, think=False,
                           keep_alive=0, options=dict(num_ctx=cfg.context_window,
                           num_predict=cfg.max_output_tokens, temperature=0, seed=42))
            if request.output_schema():
                payload['format'] = request.output_schema()
            # Same cross-process operation lock as ingestion/OCR. No new scheduler.
            with self.operation_lock():
                if self.ocr_model:
                    loaded, _ = self.transport('/api/ps', timeout=10)
                    if any(m.get('name') == self.ocr_model for m in loaded.get('models', [])):
                        self.transport('/api/generate', dict(model=self.ocr_model, stream=False,
                                       keep_alive=0), timeout=cfg.timeout)
                result.request_count = 1
                data, _ = self.transport('/api/chat', payload, timeout=cfg.timeout)
            if data.get('error'):
                raise RuntimeUnavailable('OLLAMA_ERROR')
            result.finish_reason = data.get('done_reason')
            content = data.get('message', {}).get('content')
            if not isinstance(content, str) or data.get('done') is not True:
                raise ValueError('Invalid response')
            result.text = content.strip()
            result.usage = {target: data[source] for source, target in
                            [('prompt_eval_count', 'prompt_tokens'), ('eval_count', 'completion_tokens')]
                            if type(data.get(source)) is int}
            result.status = ('TRUNCATED' if result.finish_reason == 'length' else
                             'SUCCESS' if result.text and result.finish_reason == 'stop' else 'ERROR')
            if result.status != 'SUCCESS':
                result.error = {'code': 'OUTPUT_LIMIT' if result.status == 'TRUNCATED' else 'INVALID_RESPONSE'}
        except Exception:
            # Never include server bodies, document text or credentials in failures.
            result.status = 'LOCAL_GENERATION_UNAVAILABLE'
            result.error = {'code': 'LOCAL_GENERATION_UNAVAILABLE'}
        finally:
            result.duration_ms = (time.perf_counter() - started) * 1000
        return result
