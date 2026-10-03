"""Corpus-specific, reproducible discovery. No web, generation, OCR or DB writes.

Semantic question plans are based on inspected canonical evidence, not RAG answers.
All plans resolve exact spans, are validated, scored and selected before dry-run.
"""
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

OUT=Path('data/evaluation')
SNAP=OUT/'day24_discovery_snapshot.json'


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


class Discovery:
    def __init__(self,data):
        self.data=data;self.docs=data['documents'];self.pool=[]
        self.chunks={c['chunk_id']:c for c in data['chunks']}
        self.by_block={}
        for c in data['chunks']:
            for bid in c['source_block_ids']:self.by_block.setdefault(bid,[]).append(c)

    def source(self,di,bi,quote):
        d=self.docs[di];b=d['blocks'][bi]
        assert quote in b['text'],(di,bi,quote)
        start=b['text'].index(quote);chunks=self.by_block.get(b['block_id'],[])
        chunks=sorted(chunks,key=lambda c:(c['strategy']!='structure',c['chunk_index']))
        meta=self.data['recognition'][b['recognition_id']]
        return dict(document_id=d['document_id'],file_title=d['file_name'],file_name=d['file_name'],
            page=b['page_number'],page_start=b['page_number'],page_end=b['page_number'],section=b.get('section',''),
            chunk_id=chunks[0]['chunk_id'] if chunks else None,chunk_ids=[c['chunk_id'] for c in chunks],
            source_block_id=b['block_id'],exact_quote=quote,exact_match=True,recognition_status=meta['status'],
            quote_locator=dict(block_id=b['block_id'],recognition_id=b['recognition_id'],start=start,end=start+len(quote)),
            canonical_block_sha256=sha(b['text']))

    def add(self,category,question,claims,*,subtype='',confidence=.96,review=False,notes='',ambiguity='',forbidden=None,repair=False):
        # claims: (text, [(doc ordinal, block ordinal, exact quote), ...]).
        sources=[];expected=[]
        for i,(text,spans) in enumerate(claims,1):
            refs=[]
            for span in spans:
                source=self.source(*span)
                if source not in sources:sources.append(source)
                refs.append(copy.deepcopy(source))
            expected.append(dict(claim_id=f'C{i}',text=text,supporting_sources=refs))
        reliable=all(s['recognition_status']=='SUCCESS' for s in sources)
        manual=review or not reliable
        modes=['SOURCE_RETRIEVAL','QUOTE_EXACTNESS','CLAIM_SUPPORT','DANGLING_SOURCE_ID']
        if category=='MULTI_CLAIM':modes+=['MULTI_CLAIM_SUPPORT']
        if category=='WEAK_AMBIGUOUS':modes+=['WEAK_CONTEXT_REFUSAL']
        if repair:modes+=['REPAIR_FLOW']
        if category=='UNANSWERABLE':modes=['UNANSWERABLE_REFUSAL','WEAK_CONTEXT_REFUSAL']
        record=dict(candidate_id='D24-'+sha([category,question])[:12],category=category,question=question,
            expected_answer=' '.join(c['text'] for c in expected) if expected else 'INSUFFICIENT_CONTEXT — не знаю по имеющимся документам.',
            expected_claims=expected,expected_sources=sources,difficulty='easy' if category=='FACTUAL' else 'hard',
            fact_type=subtype,why_good_for_day24=notes or 'Проверяемый факт с точным каноническим фрагментом.',
            confidence=confidence,manual_review_required=manual,discovery_notes=notes,
            ambiguity=ambiguity,forbidden_claims=forbidden or [],good_for_repair_test=repair,
            source_sufficiency='NO_SOURCE_EXPECTED' if category=='UNANSWERABLE' else 'PARTIAL_ONLY' if category=='WEAK_AMBIGUOUS' else 'MULTIPLE_SOURCES_REQUIRED' if len(sources)>1 else 'ONE_SOURCE_SUFFICIENT',
            failure_modes_tested=modes,recognition_reliable=reliable,expected_unanswerable=category=='UNANSWERABLE')
        components=dict(certainty=round(confidence*40,2),exact_evidence=20 if sources and all(s['exact_match'] for s in sources) else 0,
            recognition=10 if reliable else 0,clear_scope=10,multi_claim=8 if len(expected)>1 else 0,
            cross_document=5 if len({s['document_id'] for s in sources})>1 else 0,
            multi_page=3 if len({(s['document_id'],s['page']) for s in sources})>1 else 0,
            review_penalty=-100 if manual else 0)
        record.update(score_components=components,candidate_score=sum(components.values()))
        self.pool.append(record);return record

    def factual(self,di,bi,quote,question,answer,subtype,**kw):
        return self.add('FACTUAL',question,[(answer,[(di,bi,quote)])],subtype=subtype,**kw)

    def discover(self):
        f=self.factual
        f(0,6,'продлить срок конкурсного производства в отношении ООО "РУСМЕТ" на 6 месяцев.',
          'На какой срок заявитель просит продлить конкурсное производство ООО «РУСМЕТ» в ходатайстве от 24 февраля 2025 года?',
          'Просит продлить на 6 месяцев.','duration',confidence=.99,notes='Просьба заявителя; не утверждать, что суд уже продлил срок.')
        f(0,2,'Погосян Лилия Гамлетовна','Кто указан заявителем в ходатайстве ООО «РУСМЕТ» от 24 февраля 2025 года?',
          'Погосян Лилия Гамлетовна.','person',confidence=.97)
        f(0,7,'Копия отчета конкурсного управляющего.','Что приложено к ходатайству о продлении конкурсного производства ООО «РУСМЕТ» от 24 февраля 2025 года?',
          'Копия отчета конкурсного управляющего.','attachment',confidence=.96)
        f(0,3,'Дело № А60-3101/2024','В каком деле подано ходатайство ООО «РУСМЕТ» от 24 февраля 2025 года?',
          'В деле № А60-3101/2024.','case_number',confidence=.96)
        f(1,72,self.docs[1]['blocks'][72]['text'],'Какую сумму убытков просит взыскать Банк Российский Кредит в конкурсную массу авиакомпании «Вим-Авиа»?',
          'Заявлена сумма 4 104 326 405,76 рублей. Это требование заявителя, а не присуждённая сумма.','amount',confidence=.99,
          notes='Различает просительную часть и установленный судом факт взыскания.')
        f(1,72,self.docs[1]['blocks'][72]['text'],'С кого Банк Российский Кредит просит солидарно взыскать убытки в деле о банкротстве «Вим-Авиа»?',
          'С Максимова А.Н., Кононова В.Ю. и Стручалиной А.В.','respondents',confidence=.97)
        f(2,17,'прошу исправить данную ошибку и внести изменения в реестр требований кредиторов.',
          'Какого изменения просит Дорошенко Владимир Евгеньевич в конце заявления о выплате заработной платы?',
          'Просит исправить ошибку и внести изменения в реестр требований кредиторов.','requested_action',confidence=.94)
        f(2,14,self.docs[2]['blocks'][14]['text'],'За кем, по заявлению Дорошенко, ошибочно числятся удержанные алименты в реестре?',
          'По заявлению — за Померанц Е.А.','person',confidence=.85,review=True,notes='В соседнем блоке фамилия распознана как «Померани»; нужна проверка написания.')
        f(3,7,self.docs[3]['blocks'][7]['text'],'Когда изготовлено в полном объёме определение по делу «Вим-Авиа», резолютивная часть которого объявлена 15 февраля 2022 года?',
          '25 февраля 2022 года.','date',confidence=.94,notes='Чистый заголовок страницы 1; поздние искажённые страницы не используются.')
        f(4,67,self.docs[4]['blocks'][67]['text'],'Как апелляция разрешила жалобу на определение от 14 марта 2024 года по делу ООО «Русмет»?',
          'Определение в обжалуемой части оставлено без изменения, жалоба — без удовлетворения.','decision',confidence=.97)
        f(4,60,self.docs[4]['blocks'][60]['text'],'Какую оценку дала апелляционная коллегия договору уступки от 27 февраля 2024 года по делу ООО «Русмет»?',
          'Пришла к выводу о соответствии договора требованиям статей 383–384, 388–389 ГК РФ.','reason',confidence=.93)
        f(5,139,'Признаки ПРЕДНАМЕРЕННОГО банкротства УСТАНОВЛЕНЫ.',
          'Какой вывод о признаках преднамеренного банкротства сформулирован в заключении по нефтяной компании?',
          'В заключении указано, что признаки преднамеренного банкротства установлены.','expert_opinion',confidence=.80,review=True,
          notes='Название должника распознано несколькими несовпадающими способами; вывод автора не является решением суда.')
        f(5,153,self.docs[5]['blocks'][153]['text'],'Когда, по заключению, прекратилась деятельность нефтяной компании?',
          'В одном фрагменте указан январь 2023 года; единственную достоверную дату установить нельзя.','date',confidence=.35,review=True,
          notes='Январь 2023 на стр.16 расходится с прекращением после ноября 2023 на стр.5. Не исправлять за автора/OCR.')
        f(6,34,self.docs[6]['blocks'][34]['text'],'На какую конкретную историческую дату ООО «ГОНЗО» просит сведения об остатке на счёте «Вим-Авиа»?',
          'На 05.04.2022 года; также просит сведения на текущую дату.','date',confidence=.93)
        f(7,58,self.docs[7]['blocks'][58]['text'],'Какой анализ оснований требования к Максимову А.Н. просит провести заявитель жалобы на управляющего «Вим-Авиа»?',
          'Документально подтвержденный анализ оснований для предъявления требования о взыскании убытков.','requested_action',confidence=.89)
        f(8,78,'Жалобу общества с ограниченной ответственностью «Техно-Сплав» (ИНН 6679066764) удовлетворить.',
          'Как суд разрешил жалобу ООО «Техно-Сплав» на бездействие управляющего «Русмет» в определении от 19 марта 2025 года?',
          'Жалоба ООО «Техно-Сплав» удовлетворена.','decision',confidence=.99)
        f(8,25,'Определением от 28.01.2025 жалоба принята к рассмотрению, судебное заседание назначено на 25.02.2025.',
          'На какую дату первоначально назначили рассмотрение жалобы ООО «Техно-Сплав», принятой 28 января 2025 года?',
          'На 25 февраля 2025 года.','hearing_date',confidence=.96)
        f(9,30,self.docs[9]['blocks'][30]['text'],'Какую позицию по жалобе о неоспаривании сделок с ВУЗ банком выражает автор возражений по делу «Русмет»?',
          'Просит отказать в признании бездействия незаконным. Это позиция автора возражений.','party_position',confidence=.92)
        f(10,12,self.docs[10]['blocks'][12]['text'],'Кого суд утвердил конкурсным управляющим «Вим-Авиа» 15 февраля 2022 года?',
          'Стручалину Анастасию Валерьевну.','appointment',confidence=.97)
        f(10,13,'десятидневный срок со дня его вынесения.',
          'Какой срок обжалования указан в определении об утверждении управляющего «Вим-Авиа» от 15 февраля 2022 года?',
          'Десятидневный срок со дня вынесения определения.','appeal_period',confidence=.99)
        f(11,24,self.docs[11]['blocks'][24]['text'],'Какой срок обжалования указан в постановлении Росреестра о прекращении административного дела в отношении управляющего «Русмет»?',
          'Десятидневный срок.','appeal_period',confidence=.93)
        f(12,16,self.docs[12]['blocks'][16]['text'],'Какое бездействие Погосян заявитель первой жалобы по сделкам «Русмет» и «ВУЗ Банк» просит признать незаконным?',
          'Неподачу заявления по оспариванию сделок по выкупу задолженности ООО «Редмэджик».','requested_action',confidence=.90)
        f(13,2,'19 мая 2026 г.','Когда состоялось первое собрание кредиторов ООО «УГТНЕФРЕСУРС», отражённое в протоколе №1?',
          '19 мая 2026 года.','date',confidence=.99)
        f(13,25,'Решили: решение по вопросу не принято','Было ли принято решение по отчёту временного управляющего на первом собрании кредиторов ООО «УГТНЕФРЕСУРС»?',
          'Решение по вопросу не принято.','meeting_decision',confidence=.96)
        f(13,30,'определить саморегулируемую организацию арбитражных управляющих «ЛИГА» как саморегулируемую организацию, из числа членов которой должен быть утверждён арбитражный управляющий.',
          'Из какой СРО собрание кредиторов ООО «УГТНЕФРЕСУРС» предложило утвердить управляющего?',
          'Из СРО арбитражных управляющих «ЛИГА».','appointment',confidence=.95)
        f(14,49,self.docs[14]['blocks'][49]['text'],'Какие даты договоров поручительства «Русмет» с Международной консалтинговой компанией указаны в просьбе об их оспаривании?',
          '23.06.2023 и 05.07.2023.','contract_dates',confidence=.91)
        f(15,8,'на шесть месяцев до 26 июня 2025 года.',
          'До какой даты указан срок конкурсного производства «Вим-Авиа» в резолютивной части определения от 23 декабря 2024 года?',
          'До 26 июня 2025 года.','duration_end',confidence=.95,
          notes='Дата в чистом фрагменте; искажённый глагол «Продать» в начале блока не используется.')
        self.add('MULTI_CLAIM','Кто был утверждён управляющим «Вим-Авиа» в феврале 2022 года и до какой даты указан срок процедуры в определении от 23 декабря 2024 года?',[
            ('Управляющим утверждена Стручалина Анастасия Валерьевна.',[(10,12,self.docs[10]['blocks'][12]['text'])]),
            ('В определении от 23 декабря 2024 года указан срок до 26 июня 2025 года.',[(15,8,'на шесть месяцев до 26 июня 2025 года.')])],
            confidence=.97,notes='Два связанных судебных документа по одному делу А65-37758/2017; назначение не доказывает исполнение всех обязанностей к поздней дате.')
        self.add('MULTI_CLAIM','Какие решения первое собрание кредиторов ООО «УГТНЕФРЕСУРС» приняло о комитете кредиторов и форме дальнейших собраний?',[
            ('Комитет кредиторов решено не образовывать.',[(13,28,'Решили: комитет кредиторов не образовать.')]),
            ('Для дальнейших собраний определена заочная форма.',[(13,41,'Решили: определить заочную форму проведения дальнейших собраний кредиторов.')])],confidence=.99,notes='Независимые решения на страницах 3 и 5; одного фрагмента недостаточно.')
        self.add('MULTI_CLAIM','Кто заявитель ходатайства «РУСМЕТ» от 24 февраля 2025 года и что приложено к этому ходатайству?',[
            ('Заявитель — Погосян Лилия Гамлетовна.',[(0,2,'Погосян Лилия Гамлетовна')]),
            ('Приложена копия отчета конкурсного управляющего.',[(0,7,'Копия отчета конкурсного управляющего.')])],confidence=.95,notes='Два разнесённых блока на одной странице; не добавлять неподтверждённую оценку отчёта.')
        self.add('MULTI_CLAIM','Каковы даты объявления резолютивной части и изготовления полного определения об управляющем «Вим-Авиа» в феврале 2022 года?',[
            ('Резолютивная часть объявлена 15 февраля 2022 года.',[(3,8,self.docs[3]['blocks'][8]['text'])]),
            ('Полный текст изготовлен 25 февраля 2022 года.',[(3,7,self.docs[3]['blocks'][7]['text'])])],confidence=.94,notes='Различает два типа дат, а не считает их противоречием.')
        self.add('WEAK_AMBIGUOUS','Можно ли считать, что погашение реестра осенью 2021 года подтверждает полное перечисление удержанных алиментов Дорошенко?',[
            ('Нет: автор заявления сообщает о погашении реестра, но отдельно утверждает, что удержанные алименты полностью не перечислены.',[(2,11,self.docs[2]['blocks'][11]['text'])])],confidence=.98,
            ambiguity='Погашение реестра нельзя автоматически отождествлять с фактическим перечислением конкретных удержаний.',
            forbidden=['Все удержанные алименты перечислены в полном объёме.','Суд установил факт полного перечисления алиментов.'],repair=True,
            notes='Рядом стоят похожие, но не тождественные утверждения. Ответ должен быть ограничен позицией автора заявления.')
        self.add('WEAK_AMBIGUOUS','Означает ли прекращение Росреестром административного дела в отношении управляющего «Русмет», что судебная жалоба «Техно-Сплав» также была отклонена?',[
            ('В административном деле Росреестр прекратил производство.',[(11,23,self.docs[11]['blocks'][23]['text'])]),
            ('В отдельном судебном определении жалоба «Техно-Сплав» удовлетворена; эти результаты нельзя отождествлять.',[(8,78,'Жалобу общества с ограниченной ответственностью «Техно-Сплав» (ИНН 6679066764) удовлетворить.')])],
            confidence=.96,ambiguity='Разные виды производств и разные даты; это не противоречащие решения по одному процессу.',
            forbidden=['Жалоба Техно-Сплав отклонена.','Прекращение административного дела оправдывает все действия управляющего.'],repair=True,
            notes='Нужны административный и судебный акты. Опечатка «Погосии» сохраняется в exact quote; связь с Погосян подтверждена сопроводительным блоком документа.')
        self.add('WEAK_AMBIGUOUS','Подтверждает ли ходатайство «РУСМЕТ» от 24 февраля 2025 года, что суд уже продлил конкурсное производство на запрошенный срок?',[
            ('Само ходатайство содержит просьбу продлить срок на 6 месяцев; оно не является решением об удовлетворении этой просьбы.',[(0,6,self.docs[0]['blocks'][6]['text'])])],
            confidence=.96,ambiguity='Просьба стороны не равна резолютивной части судебного акта.',forbidden=['Суд уже продлил срок на 6 месяцев на основании этого ходатайства.'],repair=True)
        self.add('WEAK_AMBIGUOUS','Можно ли по заявлению Банка Российский Кредит считать доказанным, что с управляющих «Вим-Авиа» уже взысканы заявленные убытки?',[
            ('Документ содержит требование о солидарном взыскании, а не результат рассмотрения этого требования.',[(1,72,self.docs[1]['blocks'][72]['text'])])],
            confidence=.95,ambiguity='Формулировка «Взыскать» находится после «ПРОСИМ СУД», а не после судебного «ОПРЕДЕЛИЛ».',
            forbidden=['Убытки уже взысканы вступившим в силу решением суда.'],repair=True)
        self.add('UNANSWERABLE','Какой адрес электронной почты Дорошенко Владимира Евгеньевича указан для обратной связи по его заявлению о выплате заработной платы?',[],
            confidence=.92,notes='Контактный атрибут конкретного заявителя. Проверяется весь normalized corpus: имена, варианты написания, email/электронная почта и все адреса с @. Почтовый адрес не является email.')
        return self.pool

    def select(self):
        selected=[];used_docs=set();used_types=set()
        for category,count in [('FACTUAL',5),('MULTI_CLAIM',2),('WEAK_AMBIGUOUS',2),('UNANSWERABLE',1)]:
            eligible=[c for c in self.pool if c['category']==category and not c['manual_review_required']]
            for _ in range(count):
                def score(c):
                    ds={s['document_id'] for s in c['expected_sources']}
                    return c['candidate_score']+4*len(ds-used_docs)+(3 if c['fact_type'] and c['fact_type'] not in used_types else 0)
                winner=max(eligible,key=lambda c:(score(c),c['confidence'],c['candidate_id']))
                eligible.remove(winner);winner['selection_score']=score(winner);selected.append(copy.deepcopy(winner))
                used_docs.update(s['document_id'] for s in winner['expected_sources']);used_types.add(winner['fact_type'])
        for i,c in enumerate(selected,1):c['question_id']=c['candidate_id'];c['selection_order']=i
        return selected


def verify(data, candidates):
    blocks={b['block_id']:b for d in data['documents'] for b in d['blocks']}
    chunks={c['chunk_id']:c for c in data['chunks']}
    for c in candidates:
        for s in c['expected_sources']:
            b=blocks[s['source_block_id']];loc=s['quote_locator']
            assert b['document_id']==s['document_id']
            assert b['page_number']==s['page']==s['page_start']==s['page_end']
            assert b['text'][loc['start']:loc['end']]==s['exact_quote']
            assert sha(b['text'])==s['canonical_block_sha256']
            assert b['recognition_id']==loc['recognition_id']
            if s['chunk_id']:
                chunk=chunks[s['chunk_id']]
                assert chunk['document_id']==s['document_id'] and b['block_id'] in chunk['source_block_ids']
                assert chunk['page_start']<=s['page']<=chunk['page_end']
        for claim in c['expected_claims']:
            assert claim['supporting_sources'] and all(s in c['expected_sources'] for s in claim['supporting_sources'])
        c.setdefault('precheck',{}).update(dict(expected_sources_available=bool(c['expected_sources']) or c['expected_unanswerable'],
            expected_quotes_exact=all(s['exact_match'] for s in c['expected_sources']),
            recognition_reliable=c['recognition_reliable'],citations_suitable=bool(c['expected_sources']),
            grounding_suitable=True,question_does_not_contain_expected_answer=all(x['text'].casefold() not in c['question'].casefold() for x in c['expected_claims'])))


def main():
    data=json.loads(SNAP.read_text(encoding='utf-8'));engine=Discovery(data);pool=engine.discover();selected=engine.select()
    assert len({c['question'] for c in pool})==len(pool)
    verify(data,pool);verify(data,selected)
    manifest=[]
    for d in data['documents']:
        for p in d['pages']:
            bs=[b for b in d['blocks'] if b['page_number']==p['page_number']]
            text='\n'.join(b['text'] for b in bs)
            labels={label:len(re.findall(pattern,text,re.I)) for label,pattern in {
                'dates':r'\b\d{1,2}[.]\d{1,2}[.]\d{4}\b|\b20\d{2}\s*г',
                'amounts':r'руб|коп|млрд|млн','decisions':r'решил|определил|постановил|удовлетвор|отказ|прекрат',
                'requests':r'прошу|просим|ходатайств','obligations':r'обяз|срок|представить|взыскать'}.items()}
            manifest.append(dict(document_id=d['document_id'],file_title=d['file_name'],page=p['page_number'],status=p['status'],
                blocks=len(bs),characters=len(text),text_sha256=sha(text),fact_signals=labels))
    summary=dict(corpus_id=data['corpus']['corpus_id'],corpus_hash=data['corpus']['corpus_hash'],run_id=data['run_id'],
        documents=len(data['documents']),pages=len(manifest),blocks=sum(len(d['blocks']) for d in data['documents']),
        characters=sum(len(b['text']) for d in data['documents'] for b in d['blocks']),
        candidates=len(pool),categories=dict(Counter(c['category'] for c in pool)),
        selected_categories=dict(Counter(c['category'] for c in selected)),manual_review_candidates=[c['candidate_id'] for c in pool if c['manual_review_required']],
        selected_ground_truth_hash=sha([{k:c[k] for k in ('candidate_id','question','expected_answer','expected_claims','expected_sources')} for c in selected]))
    for name,value in [('day24_candidates.json',pool),('day24_control_questions.json',selected),('day24_discovery_summary.json',summary),('day24_corpus_scan.json',manifest)]:
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for c in selected:print(c['category'],c['question'])


if __name__=='__main__':main()
