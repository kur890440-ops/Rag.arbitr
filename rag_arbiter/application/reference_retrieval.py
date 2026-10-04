"""Canonical bounded binding of prior grounded claim identities."""
from .chat_models import ChatValidationError


class ReferenceAwareRetrievalService:
    @staticmethod
    def bind(store,context,reference,limit):
        wanted=reference.candidate_prior_claim_ids
        if len(wanted)>limit:raise ChatValidationError('Слишком много ранее перечисленных утверждений. Уточните набор для проверки.')
        found={}
        for ref in context.referents_json:
            if ref.referent_id not in reference.resolved_referents:continue
            turn=store.get('chat_turns',ref.source_turn_id)
            result=store.get('rag_comparison_runs',turn['comparison_run_id'])
            for c in result.get('claims_json',[]):
                key=ref.source_turn_id+':'+c['claim_id']
                if key in wanted and key in ref.claim_ids and c.get('support_status')=='SUPPORTED':
                    found[key]=dict(claim_id=key,claim_text=c['text'])
        if set(wanted)!=set(found) or len(wanted)!=len(set(wanted)):raise ChatValidationError('Не удалось подтвердить набор ссылок.')
        return [found[key] for key in wanted]

