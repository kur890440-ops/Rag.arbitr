"""Direct Qwen3 budget from the installed GGUF's tokenizer, without downloads.

The 512-token chat-template reserve is separate from BPE content tokens;
this is deliberately not advertised as an exact Ollama prompt count.
"""
from collections import OrderedDict
from hashlib import sha256
from threading import Lock
from tokenizers import AddedToken, Regex, Tokenizer, models, pre_tokenizers

QWEN_TEMPLATE_SHA256 = 'ae370d884f108d16e7cc8fd5259ebc5773a0afa6e078b11f4ed7e39a27e0dfc4'
# Qwen2 byte-level pre-tokenization, also used by Qwen3 GGUF.
QWEN_PATTERN = r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
_cache = OrderedDict()
_lock = Lock()


def supported(show):
    info = show.get('model_info', {})
    return (show.get('details', {}).get('family') == 'qwen3'
            and info.get('tokenizer.ggml.model') == 'gpt2'
            and info.get('tokenizer.ggml.pre') == 'qwen2'
            and info.get('tokenizer.ggml.add_bos_token') is False
            and sha256(show.get('template', '').encode()).hexdigest() == QWEN_TEMPLATE_SHA256)


def build_tokenizer(show):
    if not supported(show):
        raise ValueError('Unsupported local tokenizer/template')
    info = show['model_info']
    vocab, merges, kinds = (info['tokenizer.ggml.' + key] for key in ('tokens', 'merges', 'token_type'))
    if not vocab or not merges or len(vocab) != len(kinds) or len(set(vocab)) != len(vocab):
        raise ValueError('Incomplete local tokenizer metadata')
    tokenizer = Tokenizer(models.BPE(vocab={v: i for i, v in enumerate(vocab)},
                                     merges=[tuple(m.split(' ', 1)) for m in merges]))
    tokenizer.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(QWEN_PATTERN), behavior='isolated'),
        pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False),
    ])
    tokenizer.add_special_tokens([AddedToken(v, special=True, normalized=False)
                                  for v, kind in zip(vocab, kinds) if kind in (3, 4)])
    return tokenizer


def input_budget(request, profile, show, digest, transport):
    from .local_diagnostics import input_budget as byte_budget
    budget = byte_budget(request, profile)
    if not supported(show):
        return budget
    # A tag can be reassigned: cache by actual model digest and endpoint.
    key = (profile.base_url, digest, QWEN_TEMPLATE_SHA256)
    with _lock:
        tokenizer = _cache.get(key) if digest else None
        if tokenizer is None:
            verbose, _ = transport('/api/show', {'model': profile.model, 'verbose': True}, timeout=30)
            tokenizer = build_tokenizer(verbose)
            if digest:
                _cache[key] = tokenizer
                while len(_cache) > 4:
                    _cache.popitem(last=False)
    messages = request.messages(prompt_version=profile.prompt_version)
    count = lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)
    total = sum(count(m['content']) for m in messages)
    system = sum(count(m['content']) for m in messages if m['role'] == 'system')
    question = count(request.question)
    required = total + budget['template_safety_reserve'] + profile.max_output_tokens
    budget.update(method='ollama_gguf_qwen2_bpe', tokenizer_model_digest=digest,
                  template_sha256=QWEN_TEMPLATE_SHA256,
                  rag_estimated_tokens=0, system_estimated_tokens=system,
                  question_estimated_tokens=question, formatting_estimated_tokens=total-system-question,
                  other_estimated_tokens=0, total_estimated_input=total,
                  estimated_required=required, estimated_remaining=profile.context_window-required)
    return budget
