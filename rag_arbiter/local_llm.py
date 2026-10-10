"""Text-only Ollama adapter implementing the existing LLMProvider contract."""
import time
import copy
from contextlib import nullcontext, contextmanager, ExitStack
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
        self._lease_active = False
        self._lease_error = False
        self._generation_requested = False
        self.cleanup_error = None

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
        self._model_show = show
        return dict(model=cfg.model, digest=model.get('digest'), details=show.get('details', {}),
                    model_context_window=max(windows), effective_context_window=cfg.context_window)

    def input_budget(self, request, inspection=None):
        from .direct_generation import DirectGenerationRequest
        from .local_diagnostics import input_budget
        if isinstance(request, DirectGenerationRequest):
            from .local_token_budget import input_budget as direct_budget
            inspection = inspection if inspection is not None else self.inspect()
            return direct_budget(request, self.config, self._model_show,
                                 inspection.get('digest'), self.transport)
        return input_budget(request, self.config)

    def generate(self, request):
        cfg = self.config
        started = time.perf_counter()
        result = LLMResult(provider='local', model=cfg.model)
        try:
            if self._lease_error:
                raise RuntimeUnavailable('RESOURCE_BUSY')
            result.diagnostics = self.inspect()
            messages = request.messages(prompt_version=cfg.prompt_version)
            result.diagnostics.update(prompt_version=cfg.prompt_version, temperature=cfg.temperature, seed=cfg.seed,
                max_output_tokens=cfg.max_output_tokens, keep_alive=cfg.keep_alive)
            budget = self.input_budget(request, result.diagnostics)
            payload = dict(model=cfg.model, messages=messages, stream=False, think=False,
                           keep_alive=cfg.keep_alive, options=dict(num_ctx=cfg.context_window,
                           num_predict=cfg.max_output_tokens, temperature=cfg.temperature, seed=cfg.seed))
            if request.output_schema():
                payload['format'] = request.output_schema()
            if request.capture_diagnostics:
                result.diagnostics['input_budget']=budget
                result.diagnostics['exact_input']=copy.deepcopy(payload)
                result.diagnostics['transport_attempted']=False
            if budget['estimated_remaining'] < 0:
                result.error = {'code': 'LOCAL_CONTEXT_LIMIT'}
                return result
            # Same cross-process operation lock as ingestion/OCR. No new scheduler.
            with (nullcontext() if self._lease_active else self.operation_lock()):
                if self.ocr_model:
                    loaded, _ = self.transport('/api/ps', timeout=10)
                    if any(m.get('name') == self.ocr_model for m in loaded.get('models', [])):
                        self.transport('/api/generate', dict(model=self.ocr_model, stream=False,
                                       keep_alive=0), timeout=cfg.timeout)
                result.request_count = 1
                self._generation_requested = True
                if request.capture_diagnostics:result.diagnostics['transport_attempted']=True
                data, _ = self.transport('/api/chat', payload, timeout=cfg.timeout)
            if data.get('error'):
                raise RuntimeUnavailable('OLLAMA_ERROR')
            result.diagnostics['ollama'] = {k: data[k] for k in (
                'prompt_eval_count', 'eval_count', 'prompt_eval_duration', 'eval_duration',
                'load_duration', 'total_duration') if type(data.get(k)) is int}
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

    def release(self):
        """End the short generation/repair lease; TTL also bounds crash recovery."""
        if not self.config.keep_alive or not self._generation_requested:
            return
        with (nullcontext() if self._lease_active else self.operation_lock()):
            loaded, _ = self.transport('/api/ps', timeout=10)
            if any(m.get('name') == self.config.model for m in loaded.get('models', [])):
                self.transport('/api/generate', dict(model=self.config.model, stream=False, keep_alive=0), timeout=15)

    @contextmanager
    def generation_session(self):
        """Reuse the existing OCR/retrieval lock across a bounded local repair lease."""
        if not self.config.keep_alive or self._lease_active:
            yield
            return
        with ExitStack() as stack:
            try:
                stack.enter_context(self.operation_lock())
            except Exception:
                self._lease_error = True
                try:
                    yield
                finally:
                    self._lease_error = False
                return
            self._lease_active = True
            try:
                yield
            finally:
                try:
                    self.release()
                except Exception:
                    self.cleanup_error = 'LOCAL_RELEASE_FAILED_TTL_BOUNDED'
                finally:
                    self._lease_active = False
