"""Read model for recorded diagnostics. Unknown counts remain unknown."""
def diagnostics_view(record,generation,snapshot):
    recorded=snapshot.get('retrieval_diagnostics') if snapshot else None
    trace=recorded or record
    known=bool(recorded or record.get('retrieval_started') or record.get('candidate_trace'))
    retrieval={key:trace.get(key) if known else None for key in ('candidate_top_n','candidates_retrieved',
        'candidates_after_cleanup','rerank_output_count','candidates_after_threshold')}
    ranked=[entry for entry in trace.get('candidate_trace') or [] if entry.get('rerank_rank') is not None]
    if ranked:
        if retrieval['rerank_output_count'] is None:retrieval['rerank_output_count']=len(ranked)
        if retrieval['candidates_after_threshold'] is None:retrieval['candidates_after_threshold']=sum(entry.get('accepted') is True for entry in ranked)
    sources=snapshot['sources'] if snapshot else record.get('sources',[])
    context=snapshot['text'] if snapshot else record.get('context_text','')
    selection=trace.get('context_selection_diagnostics') or {}
    events=selection.get('selection',[])
    dropped=[e for e in events if e['reason']=='BUDGET']
    fallback=[e for e in events if e['reason']=='BUDGET_FALLBACK']
    final_excluded=selection.get('final_builder',{}).get('budget_excluded',[])
    expansion=trace.get('expansion_budget_exclusions') or []
    reduced=bool(dropped or fallback or final_excluded or expansion) if selection else None
    rows=[]
    for index,source in enumerate(sources,1):
        rows.append(dict(number=index,source=source,text_bytes=len(source['text'].encode('utf-8')),
            strategy=trace.get('retrieval_strategy') or record.get('retrieval_strategy'),
            chunking_run_id=source.get('chunking_run_id') or trace.get('chunking_run_id')))
    attempts=[]
    raw_attempts=generation.get('generation_attempts') or generation.get('attempts') or []
    if not raw_attempts and (generation.get('result') or {}).get('diagnostics'):
        raw_attempts=[generation['result']]
    for index,raw in enumerate(raw_attempts,1):
        diagnostic=raw.get('diagnostics') or {}
        budget=diagnostic.get('input_budget')
        actual=diagnostic.get('ollama') or {}
        fit='UNKNOWN';required=None;remaining=None
        profile=generation.get('profile') or {}
        limits=budget or dict(num_ctx=profile.get('context_window'),num_predict=profile.get('max_output_tokens'))
        if type(limits.get('num_ctx')) is int and type(limits.get('num_predict')) is int:
            if type(actual.get('prompt_eval_count')) is int:
                required=actual['prompt_eval_count']+limits['num_predict']
                remaining=limits['num_ctx']-required
                fit='OVER BUDGET' if remaining<0 else 'TRUNCATED' if reduced else 'OK'
            elif budget and budget['estimated_remaining']<0:
                fit='OVER BUDGET (estimate guard)'
        attempts.append(dict(number=index,budget=budget,actual=actual,fit=fit,required=required,remaining=remaining,
            payload=diagnostic.get('exact_input'),transport_attempted=diagnostic.get('transport_attempted'),
            status=raw.get('status')))
    return dict(retrieval=retrieval,known=known,rows=rows,
        retrieval_run_id=snapshot['retrieval_run_id'] if snapshot else record['comparison_run_id'],
        final_count=len(sources) if known or snapshot else None,
        rag_estimated_tokens=len(context.encode('utf-8')) if known or snapshot else None,
        attempts=attempts,context_reduced=reduced,dropped=dropped,fallback=fallback,final_excluded=final_excluded,
        dropped_count=len(dropped)+len(final_excluded) if selection else None,
        dropped_bytes=sum(e['text_bytes'] for e in dropped+final_excluded)+sum(e['text_bytes']-e['retained_bytes'] for e in fallback) if selection else None,
        expansion_exclusions=expansion,selection_events=events,candidate_trace=trace.get('candidate_trace'),
        grounding=generation.get('grounding_result') or record.get('grounding_result'),
        grounding_trace=generation.get('grounding_diagnostics') or record.get('grounding_diagnostics'))
