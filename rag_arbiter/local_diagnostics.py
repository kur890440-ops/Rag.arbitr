"""Observability of existing byte-budget guard; never claims a model tokenizer."""

def input_budget(request,profile):
    messages=request.messages(prompt_version=profile.prompt_version)
    size=lambda text:len(text.encode('utf-8'))
    total=sum(size(m['content']) for m in messages)
    system=sum(size(m['content']) for m in messages if m['role']=='system')
    context_size=size(request.context or '')
    rag=size(request.diagnostic_rag_context if request.diagnostic_rag_context is not None else request.context or '')
    question=size(request.question)
    return dict(method='utf8_bytes_upper_estimate',exact_pre_inference=False,
        rag_estimated_tokens=rag,system_estimated_tokens=system,question_estimated_tokens=question,
        formatting_estimated_tokens=total-system-context_size-question,other_estimated_tokens=context_size-rag,
        total_estimated_input=total,template_safety_reserve=512,
        num_ctx=profile.context_window,num_predict=profile.max_output_tokens,
        estimated_required=total+512+profile.max_output_tokens,
        estimated_remaining=profile.context_window-total-512-profile.max_output_tokens,
        prompt_truncated=False,context_truncated=False,ollama_truncated=None)
