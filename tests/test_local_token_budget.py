"""Budget regressions: model metadata, byte inflation, and both admission guards."""
import copy
from hashlib import sha256
import pytest
from tokenizers import Tokenizer, Regex, AddedToken, models, pre_tokenizers, trainers
from rag_arbiter import local_token_budget as budget_module
from rag_arbiter.direct_generation import DirectGenerationRequest, DirectLocalConfig
from rag_arbiter.local_llm import LocalLLMProvider


@pytest.fixture
def qwen_metadata(monkeypatch):
    # Small trained fixture: real byte-level BPE, no network or model asset required.
    template = 'fixture template'
    monkeypatch.setattr(budget_module, 'QWEN_TEMPLATE_SHA256', sha256(template.encode()).hexdigest())
    budget_module._cache.clear()
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.Sequence([
        pre_tokenizers.Split(Regex(budget_module.QWEN_PATTERN), behavior='isolated'),
        pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
    corpus = ['Суд постановил взыскать 1000 рублей. Обстоятельства дела. ' * 100,
              'You are a helpful assistant. Answer the user accurately in the language of their question.']
    tokenizer.train_from_iterator(corpus, trainers.BpeTrainer(vocab_size=700, min_frequency=1,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=['<|im_start|>', '<|im_end|>']))
    import json
    model = json.loads(tokenizer.to_str())['model']
    vocab = sorted(model['vocab'], key=model['vocab'].get)
    show = {'template':template, 'capabilities':['completion'], 'details':{'family':'qwen3'},
            'model_info':{'qwen3.context_length':40960, 'tokenizer.ggml.model':'gpt2',
                         'tokenizer.ggml.pre':'qwen2', 'tokenizer.ggml.add_bos_token':False,
                         'tokenizer.ggml.tokens':vocab,
                         'tokenizer.ggml.token_type':[3 if t.startswith('<|') else 1 for t in vocab],
                         'tokenizer.ggml.merges':[' '.join(m) for m in model['merges']]}}
    yield show, tokenizer
    budget_module._cache.clear()


def make_provider(show, context=4096, digest='fixture'):
    calls=[]
    cfg=DirectLocalConfig(enabled=True, model='same-tag', context_window=context,
                          max_output_tokens=128, prompt_version='day29-baseline')
    def transport(path, body=None, timeout=None):
        calls.append((path,copy.deepcopy(body)))
        if path=='/api/tags':return {'models':[{'name':cfg.model, 'digest':digest}]}, ''
        if path=='/api/show':return show, ''
        if path=='/api/chat':return {'done':True,'done_reason':'stop','message':{'content':'ok'}}, ''
        return {}, ''
    return LocalLLMProvider(cfg, transport=transport), calls


def test_metadata_bpe_roundtrip_unicode_and_special(qwen_metadata):
    show, original=qwen_metadata
    rebuilt=budget_module.build_tokenizer(show)
    for text in ['Суд постановил взыскать 1000 рублей.', 'ё е\u0308 🙂\n\t 12345', '<|im_start|>user\nФакт<|im_end|>']:
        assert rebuilt.encode(text).ids==original.encode(text).ids
    invalid=copy.deepcopy(show);invalid['model_info']['tokenizer.ggml.merges']=[]
    with pytest.raises(ValueError):budget_module.build_tokenizer(invalid)


def test_russian_bytes_are_not_tokens_and_full_payload_is_sent(qwen_metadata):
    show,_=qwen_metadata
    provider,calls=make_provider(show)
    question='Суд постановил взыскать 1000 рублей. ' * 220
    request=DirectGenerationRequest(question=question)
    assert len(question.encode('utf-8'))>4096
    budget=provider.input_budget(request)
    assert budget['method']=='ollama_gguf_qwen2_bpe' and budget['estimated_remaining']>0
    result=provider.generate(request)
    assert result.status=='SUCCESS'
    payload=next(body for path,body in calls if path=='/api/chat')
    assert payload['messages'][1]['content']==question
    assert result.diagnostics['input_budget']==budget
    assert not budget['exact_pre_inference']


def test_exact_budget_boundary_and_overflow_block_transport(qwen_metadata):
    show,_=qwen_metadata
    provider,calls=make_provider(show)
    request=DirectGenerationRequest(question='Суд постановил взыскать 1000 рублей. '*500)
    required=provider.input_budget(request)['estimated_required']
    assert required>4096
    provider.config.context_window=required
    assert provider.generate(request).status=='SUCCESS'
    calls.clear();provider.config.context_window=required-1
    result=provider.generate(request)
    assert result.error['code']=='LOCAL_CONTEXT_LIMIT'
    assert not any(path=='/api/chat' for path,_ in calls)


def test_unknown_metadata_fallback_and_digest_cache_invalidation(qwen_metadata):
    show,_=qwen_metadata
    request=DirectGenerationRequest(question='Суд')
    for digest, expected_load in [('v1', True), ('v1', False), ('v2', True)]:
        provider,calls=make_provider(show,digest=digest)
        provider.input_budget(request)
        verbose=[body for path,body in calls if path=='/api/show' and body.get('verbose')]
        assert bool(verbose)==expected_load
    altered=copy.deepcopy(show);altered['template']='unknown template'
    provider,_=make_provider(altered)
    assert provider.input_budget(request)['method']=='utf8_bytes_upper_estimate'


def test_missing_supported_metadata_fails_closed(qwen_metadata):
    show,_=qwen_metadata
    show['model_info']['tokenizer.ggml.tokens']=[]
    provider,calls=make_provider(show)
    result=provider.generate(DirectGenerationRequest(question='Суд'))
    assert result.status=='LOCAL_GENERATION_UNAVAILABLE'
    assert not any(path=='/api/chat' for path,_ in calls)
