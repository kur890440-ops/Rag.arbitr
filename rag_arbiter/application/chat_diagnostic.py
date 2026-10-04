"""Read-only Markdown export of stored chat evidence, with fail-closed selection."""
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

MISSING = 'NOT RECORDED'
SENSITIVE = re.compile(r'(?i)(authorization|cookie|password|passwd|secret|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|bearer|database_url|dsn|^token$)')


def secret_values(extra=()):
    values = [v for k, v in os.environ.items() if SENSITIVE.search(k) and v]
    # Only local secret values are collected, never names or file contents in output.
    path = Path('.env')
    if path.is_file():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            key, sep, value = line.partition('=')
            if sep and SENSITIVE.search(key):
                values.append(value.strip().strip('\"\''))
    return sorted({str(v) for v in [*values, *extra] if v}, key=len, reverse=True)


def redact(value, secrets=None):
    secrets = secret_values() if secrets is None else secrets
    if isinstance(value, dict):
        return {('[REDACTED]' if SENSITIVE.search(str(k)) else str(k)):
                ('[REDACTED]' if SENSITIVE.search(str(k)) else redact(v, secrets)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if not isinstance(value, str):
        return value
    for secret in secrets:
        value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)["\']?\s*[:=][^\r\n]*', '[REDACTED]', value)
    value = re.sub(r'(?i)\bBearer\s+[^\s"\',;]+', '[REDACTED]', value)
    value = re.sub(r'(?i)\b(?:[\w-]*(?:API[_-]?KEY|PASSWORD|PASSWD|SECRET|ACCESS_TOKEN|REFRESH_TOKEN)[\w-]*)\b["\']?\s*[=:]\s*(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;]+)', '[REDACTED]', value)
    value = re.sub(r'(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', value)
    value = re.sub(r'(?i)([?&](?:token|api_key|password|secret)=)[^\s&#]+', '[REDACTED]', value)
    value = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]', value)
    value = re.sub(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[REDACTED]', value)
    value = re.sub(r'\b(?:MINIMAX_API_KEY|OPENROUTER_API_KEY)\b', '[REDACTED]', value)
    return value


def pick(data, keys):
    return {k: data.get(k, MISSING) for k in keys.split()}


def code_version():
    result = {}
    root = Path(__file__).resolve().parents[2]
    try:
        import tomllib
        result['application_version'] = tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    except (OSError, KeyError, ValueError):
        result['application_version'] = MISSING
    for key, args in [('branch', ['branch', '--show-current']), ('commit', ['rev-parse', 'HEAD']),
                      ('dirty_working_tree', ['status', '--porcelain'])]:
        try:
            p = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3)
            result[key] = (bool(p.stdout.strip()) if key == 'dirty_working_tree' else p.stdout.strip()) if p.returncode == 0 else MISSING
        except (OSError, subprocess.TimeoutExpired):
            result[key] = MISSING
    return result


class ChatDiagnosticService:
    def __init__(self, runs):
        self.runs = runs

    def build(self, session_id, focused_turn_id=None):
        with self.runs.db() as store:
            store.db.execute('BEGIN')
            session = store.get('chat_sessions', session_id)
            if not session:
                raise KeyError(session_id)
            turns = [json.loads(r[0]) for r in store.db.execute(
                "SELECT data FROM chat_turns WHERE json_extract(data,'$.session_id')=? ORDER BY rowid", (session_id,))]
            messages = {m['message_id']: m for m in (json.loads(r[0]) for r in store.db.execute(
                "SELECT data FROM chat_messages WHERE json_extract(data,'$.session_id')=? ORDER BY json_extract(data,'$.sequence')", (session_id,)))}
            if focused_turn_id:
                focused = next((t for t in turns if t['turn_id'] == focused_turn_id), None)
                if focused is None:
                    raise KeyError(focused_turn_id)
            else:
                completed = [t for t in turns if t.get('assistant_message_id') and t['status'] not in ('RUNNING', 'QUEUED')]
                focused = completed[-1] if completed else {}
            trace = focused.get('trace', {})
            comparison = store.get('rag_comparison_runs', focused.get('comparison_run_id', '')) or {}
            state = store.get('task_states', session_id) or {}
            working = store.get('dialogue_working_contexts', session_id) or {}
            before = trace.get('dialogue_context_before', {})
            prior = []
            source_turn_ids = set(before.get('source_turn_ids_json', []))
            for ref in before.get('referents_json', []):
                tid = ref.get('source_turn_id');source_turn_ids.add(tid)
                old = next((t for t in turns if t['turn_id'] == tid), None)
                if not old:
                    continue
                record = store.get('rag_comparison_runs', old.get('comparison_run_id', '')) or {}
                for claim in record.get('claims_json', []):
                    qualified = str(tid) + ':' + claim['claim_id']
                    if qualified not in ref.get('claim_ids', []):
                        continue
                    prior.append({**pick(claim, 'claim_id text support_status supporting_source_ids support_scores reason'),
                        'qualified_claim_id': qualified, 'source_turn_id': tid, 'grounding_status': old.get('grounding_status'),
                        'citations_count': sum(c.get('claim_id') == claim['claim_id'] for c in record.get('citations_json', []))})
            policy = trace.get('retrieval_policy_json', {})
            metadata = policy.get('document_metadata', {})
            eligible = trace.get('eligible_document_ids', [])
            titles = {}
            for did in set(eligible[:100]) | {s.get('document_id') for s in comparison.get('retrieved_sources', [])}:
                doc = store.get('documents', did) if did else None
                if doc:
                    titles[did] = doc.get('file_name', MISSING)
        # All further work is on the consistent read snapshot; no pipeline execution.
        events = trace.get('diagnostic_events', [])
        def event(kind):
            return [e for e in events if e.get('kind') == kind] or MISSING
        def limited(items, n=20):
            return {'total': len(items), 'shown': min(len(items), n), 'items': items[:n]}
        sections = []
        def section(name, data):
            sections.append((name, data))
        section('SESSION', {**pick(session, 'session_id title created_at updated_at'), 'generated_at': datetime.now(timezone.utc).isoformat(),
            'project': 'rag.арбитр', 'messages_count': len(messages), 'turns_count': len(turns),
            'focused_turn_id': focused.get('turn_id', MISSING), 'focused_user_message_id': focused.get('user_message_id', MISSING),
            'focused_assistant_message_id': focused.get('assistant_message_id', MISSING)})
        chosen = {t['turn_id'] for t in turns[-15:]} | source_turn_ids | {focused.get('turn_id')}
        history = []
        for n, t in enumerate(turns, 1):
            if t['turn_id'] in chosen:
                history.append({'TURN': n, 'turn_id': t['turn_id'], 'USER': messages.get(t['user_message_id'], {}).get('content', MISSING),
                    'ASSISTANT': messages.get(t.get('assistant_message_id'), {}).get('content', MISSING),
                    'status': t['status'], 'grounding': t.get('grounding_status', MISSING)})
        section('CHAT HISTORY', history)
        section('FOCUSED TURN', {**pick(focused, 'turn_id status created_at latency_ms stage'),
            'started_at': comparison.get('started_at', MISSING), 'finished_at': comparison.get('finished_at', MISSING),
            'timing_scope': 'comparison timestamps; latency_ms is full chat turn',
            'USER MESSAGE': messages.get(focused.get('user_message_id'), {}).get('content', MISSING),
            'ASSISTANT RESULT': messages.get(focused.get('assistant_message_id'), {}).get('content', MISSING)})
        section('TASK STATE', {'focused_state': trace.get('task_state_after', MISSING), 'current_session_state': state})
        section('TASK STATE CHANGE', pick(trace, 'task_state_before task_state_patch task_state_after'))
        section('DIALOGUE WORKING CONTEXT', {'before_focused_turn': before or MISSING, 'current_session': working or MISSING})
        section('PRIOR GROUNDED CLAIMS AVAILABLE TO THIS TURN', prior if before else MISSING)
        section('REFERENCE RESOLUTION', trace.get('reference_resolution_json', MISSING))
        section('REFERENCE RESOLVER INPUTS', event('DAY25_REFERENCES'))
        section('CONTEXTUAL QUERY BUILDER', {'captured': event('contextual_builder'), **pick(trace, 'resolution reference_prior_claims semantic_constraints_json goal_injected_into_retrieval')})
        section('QUESTION INTENT / ANSWER MODE', {'result': trace.get('question_intent_json', MISSING), 'inputs': event('DAY25_ANSWER_MODE')})
        section('RETRIEVAL POLICY', {**pick(policy, 'task_state_version scope include_filters exclude_filters date_filters semantic_constraints unsupported_constraints'),
            'stored_policy_filter': trace.get('hard_filters_json', MISSING), 'actual_qdrant_calls': event('qdrant_search')})
        section('ELIGIBLE DOCUMENTS', {'corpus_documents_total': len(policy['corpus_document_ids']) if 'corpus_document_ids' in policy else MISSING,
            'eligible_documents_count': len(eligible) if 'eligible_document_ids' in trace else MISSING,
            'shown_limit': 100, 'documents': [{'document_id': did, 'file_title': titles.get(did, MISSING),
                **pick(metadata.get(did, {}), 'document_type document_date document_year confidence classification_status')} for did in eligible[:100]]})
        section('QUERY PIPELINE', {**pick(focused, 'original_question resolved_question retrieval_query'),
            'pre_rewrite_retrieval_query': trace.get('search_question', MISSING), 'rewrite': trace.get('rewrite', comparison.get('rewrite_result', MISSING))})
        section('QUERY REWRITE GUARD', event('rewrite_guard'))
        section('REFERENCE-AWARE RETRIEVAL', {**pick(trace, 'max_reference_claims reference_prior_claims reference_queries reference_searches reference_verification_results'),
            'mode': 'claim_only_fresh' if trace.get('reference_queries') else 'reuse_evidence' if trace.get('policy_validation_results') else 'none'})
        section('POLICY REVALIDATION', pick(trace, 'policy_validation_results reused_claims fresh_retrieval_calls'))
        section('PREDICATE EVALUATION', pick(trace, 'predicate_evaluation_results reference_answer_status'))
        section('VECTOR RETRIEVAL', {**pick(comparison, 'embedding_model retrieval_strategy chunking_run_id index_versions_used candidate_top_n vector_retrieval_duration_ms'), 'actual_calls': event('qdrant_search')})
        candidates = []
        for h in comparison.get('retrieved_sources', [])[:20]:
            candidates.append({**pick(h, 'rank chunk_id document_id file_name page_start page_end section score'),
                'document_type': metadata.get(h.get('document_id'), {}).get('document_type', MISSING), 'content_preview': h.get('text', '')[:400]})
        section('RAW CANDIDATES', {'total': comparison.get('retrieved_count', MISSING), 'shown_limit': 20, 'items': candidates})
        section('HEURISTIC CLEANUP', pick(comparison, 'candidates_retrieved candidates_after_cleanup candidates_after_dedup dedup_diagnostics'))
        section('RERANKING', {**pick(comparison, 'reranker_model reranker_settings reranker_status rerank_threshold rerank_duration_ms candidates_after_rerank'),
            'candidates': limited(comparison.get('candidate_trace', []))})
        section('CONTEXT EXPANSION', limited(comparison.get('context_candidate_metadata_json', [])))
        sources = comparison.get('sources', focused.get('sources_json', []))
        section('FINAL CONTEXTS SENT TO LLM', {'note': 'Stored selected context; actual request recorded below when available.',
            **pick(comparison, 'contexts_used context_tokens context_document_ids'),
            'sources': [{**pick(s, 'reference source_id document_id file_name page_start page_end section anchor_chunk_ids score rerank_score expansion_type text'),
                'document_type': metadata.get(s.get('document_id'), {}).get('document_type', MISSING)} for s in sources]})
        section('LLM GENERATION', pick(comparison, 'llm_provider llm_model original_question generation_settings_json chat_generation_question context_tokens rag_duration_ms'))
        section('ACTUAL GENERATION REQUESTS', {'standard_generation': event('generation_request'), 'reference_verifier': event('DAY25_CLAIM_PREDICATE'),
            'predicate_evaluator': event('DAY25_PREDICATE_EVALUATION'),
            'note': 'Exhaustive page payloads intentionally excluded; no full corpus dump.'})
        section('GENERATION RESULT', {'captured': event('generation_result'), **pick(comparison, 'rag_result rag_answer full_document_status full_document_answer')})
        section('GROUNDING', {**pick(focused, 'grounding_status'), **pick(comparison, 'grounding_result grounding_diagnostics refusal_reason repair_used')})
        section('CLAIM DETAILS', comparison.get('claims_json', trace.get('claims_json', MISSING)))
        section('CITATIONS', comparison.get('citations_json', focused.get('citations_json', MISSING)))
        section('INSUFFICIENT CONTEXT DIAGNOSIS', {'applicable': focused.get('grounding_status') == 'INSUFFICIENT_CONTEXT',
            'recorded_reason': comparison.get('refusal_reason', MISSING), **pick(trace, 'retrieval_counts reference_verification_results'),
            **pick(comparison, 'candidates_retrieved candidates_after_cleanup candidates_after_rerank contexts_used claims_supported'),
            'stage_inference': 'Not inferred; inspect recorded reasons and counts.'})
        section('ERRORS / WARNINGS', {'chat': pick(trace, 'error warning dialogue_failure rag_error exhaustive_error memory_extraction'),
            'comparison': pick(comparison, 'error_json reference_verification_error rewrite_result')})
        section('TIMINGS', {**{k: 'N/A' for k in ('task_memory_ms','policy_build_ms','reference_resolution_ms',
            'query_resolution_ms','mode_routing_ms','embedding_ms','heuristics_ms','context_build_ms','grounding_ms','repair_ms')},
            'rewrite_ms': comparison.get('rewrite_duration_ms','N/A'), 'rerank_ms': comparison.get('rerank_duration_ms','N/A'),
            'qdrant_ms': 'N/A', 'embedding_plus_vector_retrieval_ms': comparison.get('vector_retrieval_duration_ms','N/A'),
            'generation_ms': comparison.get('rag_duration_ms','N/A'),
            'total_ms': focused.get('latency_ms', 'N/A'), 'stage_checkpoints_ms': trace.get('stages', MISSING),
            'recorded_durations': trace.get('durations', MISSING),
            'unmeasured_individual_durations': 'N/A (checkpoints are not independent stage durations)'})
        section('COMPONENTS', {'recorded_reranker': comparison.get('reranker_status', MISSING),
            'embedding_model': comparison.get('embedding_model', MISSING), 'MiniMax': pick(comparison, 'llm_model'),
            'Qwen': MISSING, 'Qdrant_version_status': MISSING, 'embedding_device': MISSING, 'SQLite_export_read': 'OK'})
        section('CODE VERSION', {'at_export': code_version(), 'at_turn': trace.get('code_version', MISSING)})
        # Explicit allowlist above: never serialize config_snapshot, indexes, vectors or DB.
        secrets = secret_values([self.runs.config.llm.resolved_key()])
        output = ['# rag.арбитр · CHAT DIAGNOSTIC', '', 'Stored evidence only. NOT RECORDED = not saved; N/A = not measured. Secrets replaced by [REDACTED].']
        for title, data in sections:
            clean = redact(data, secrets)
            output.extend(['', '## ' + title, ''])
            if isinstance(clean, dict):
                entries = clean.items()
            elif isinstance(clean, list):
                entries = [(str(i + 1), item) for i, item in enumerate(clean)]
            else:
                entries = [('value', clean)]
            for key, value in entries:
                if isinstance(value, (dict, list)):
                    text = json.dumps(value, ensure_ascii=False, indent=2)
                else:
                    text = str(value)
                fence = '`' * max(3, max((len(m.group()) + 1 for m in re.finditer(r'`+', text)), default=3))
                output.extend([f'{key}:', fence, text, fence, ''])
        return '\n'.join(output)
