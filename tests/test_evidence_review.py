import json
import pytest
from rag_arbiter.application.evidence_review import EvidenceReview
from rag_arbiter.llm import LLMConfig, LLMResult


def reviewer(rows):
    class Provider:
        def generate(self, request):
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(decisions=rows)))
    return EvidenceReview(lambda cfg:Provider(),LLMConfig(),'Какие обязанности у стороны договора?')


def decision(key,status='SUPPORTED',evidence=None):
    return dict(id=key,status=status,evidence=evidence or [],reason='test')


def test_exact_evidence_and_semantic_decision_required():
    items=[dict(id='1',texts=['ООО «Север» обязано представить отчет.']),dict(id='2',texts=['Другой участник получает оплату.'])]
    review=reviewer([decision('I1',evidence=['E1_1']),decision('I2','UNRELATED')])
    assert review.check('SELECT',items)=={'1'}
    assert 'evidence' not in review.trace[0]['decisions'][0]


@pytest.mark.parametrize('rows',[
    [decision('invented',evidence=['E1_1'])],[],
    [decision('I1',evidence=['E1_1']),decision('I1',evidence=['E1_1'])],
    [decision('I1',evidence=['invented quote'])], [decision('I1')]])
def test_malformed_or_fabricated_evidence_fails_closed(rows):
    with pytest.raises(ValueError):reviewer(rows).check('SELECT',[dict(id='1',texts=['text'])])


def test_ambiguous_relation_cannot_be_accepted():
    assert reviewer([decision('I1','AMBIGUOUS')]).check('SELECT',[dict(id='1',texts=['Два однофамильца. Он обязан...'])])==set()


def test_final_review_cannot_borrow_evidence_from_other_claim():
    review=reviewer([decision('I1',evidence=['Чужой факт.'])])
    with pytest.raises(ValueError):review.validate([dict(claim_id='C1',text='Факт.')],[dict(claim_id='C2',quote='Чужой факт.',exact_match=True)])


def test_nonexact_citations_never_enter_final_review():
    review=reviewer([decision('I1',evidence=['Факт.'])])
    with pytest.raises(ValueError):review.validate([dict(claim_id='C1',text='Факт.')],[dict(claim_id='C1',quote='Факт.',exact_match=False)])


def test_large_review_batches_all_evidence_without_truncation():
    seen=[]
    class Provider:
        def generate(self,request):
            assert sum(len(m['content'].encode('utf-8')) for m in request.messages())+512<=10000
            data=json.loads(request.question[request.question.index('{"question":'):]);seen.extend(data['items'])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(decisions=[decision(x['id'],evidence=x['evidence_ids']) for x in data['items']])))
    items=[dict(id=str(i),texts=['Тестовый договор и сведения. '*100]) for i in range(20)]
    r=EvidenceReview(lambda c:Provider(),LLMConfig(full_document_context_budget=14096),'Что известно?',input_budget=10000)
    assert r.check('SELECT',items)=={x['id'] for x in items}
    assert [x['texts'] for x in seen]==[x['texts'] for x in items]
    assert len(r.trace)>1


def test_oversized_single_item_fails_before_any_call():
    def factory(cfg):raise AssertionError('Must not call provider')
    r=EvidenceReview(factory,LLMConfig(full_document_context_budget=14096),'Что известно?',input_budget=10000)
    with pytest.raises(ValueError,match='EVIDENCE_ITEM_EXCEEDS_INPUT_BUDGET'):
        r.check('SELECT',[dict(id='small',texts=['text']),dict(id='large',texts=['текст '*10000])])


def test_short_ids_preserve_source_mapping_and_immutable_text():
    originals=[dict(id='a'*64,texts=['  ООО «Тест»\u00a0— 7 %;\nстрока 2.']),dict(id='b'*64,texts=['Другой факт.'])]
    seen=[]
    class Provider:
        def generate(self,request):
            data=json.loads(request.question[request.question.index('{"question":'):]);seen.extend(data['items'])
            return LLMResult(model='fixture',status='SUCCESS',text=json.dumps(dict(decisions=[decision('I2','UNRELATED'),decision('I1',evidence=['E1_1'])])))
    r=EvidenceReview(lambda c:Provider(),LLMConfig(),'Какой размер?')
    assert r.check('SELECT',originals)=={'a'*64}
    assert seen[0]['texts']==originals[0]['texts']
    assert seen[0]['id']=='I1' and seen[1]['id']=='I2'


def test_cross_item_reference_rejected_and_error_recorded():
    r=reviewer([decision('I1',evidence=['E2_1']),decision('I2','UNRELATED')])
    with pytest.raises(ValueError,match='INVALID_REVIEW_EVIDENCE_REF'):
        r.check('SELECT',[dict(id='a',texts=['Первый факт.']),dict(id='b',texts=['Чужой факт.'])])
    assert r.trace[-1]['error_code']=='INVALID_REVIEW_EVIDENCE_REF'


def test_partial_review_preserves_unique_valid_siblings():
    items=[dict(id=str(i),texts=['Canonical text.']) for i in range(3)]
    r=reviewer([decision('I1',evidence=['E1_1']),decision('I2',evidence=['invented']),decision('I3',evidence=['E3_1'])])
    assert r.check('CLAIMS',items,allow_partial=True)=={'0','2'}
    assert any(t.get('error_code')=='INVALID_REVIEW_EVIDENCE_REF' for t in r.trace)


def test_partial_review_missing_id_does_not_erase_other_decisions():
    items=[dict(id=str(i),texts=['Canonical text.']) for i in range(3)]
    r=reviewer([decision('I1',evidence=['E1_1']),decision('I3',evidence=['E3_1'])])
    assert r.check('CLAIMS',items,allow_partial=True)=={'0','2'}
