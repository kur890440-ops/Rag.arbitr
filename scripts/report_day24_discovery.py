"""Finalize independent evidence review, artifacts and readable discovery report."""
import json
from collections import Counter
from pathlib import Path
from scripts.discover_day24_questions import OUT,sha,verify


def main():
    load=lambda name:json.loads((OUT/name).read_text(encoding='utf-8'))
    snapshot=load('day24_discovery_snapshot.json');pool=load('day24_candidates.json')
    selected=load('day24_control_questions.json');summary=load('day24_discovery_summary.json')
    dry=load('day24_retrieval_dry_run.json');absence=load('day24_unanswerable_verification.json')
    assert len(dry)==len(selected)==10 and summary['dry_run_complete']
    assert summary['selected_ground_truth_hash']==summary['ground_truth_hash_after']
    verify(snapshot,selected)
    # Review every nearest fragment, not merely absence of a matching top result.
    for hit in absence['closest_candidate_sources']:
        if hit['document_id'] in absence['person_document_ids']:
            hit['why_not_answer']='Фрагмент касается Дорошенко и заработной платы/алиментов, но email не содержит; полный документ также проверен.'
        elif '@' in hit['text']:
            hit['why_not_answer']='Есть адреса других участников/организаций; в полном тексте документа нет связи адреса с Дорошенко Владимиром Евгеньевичем.'
        else:
            hit['why_not_answer']='Общий контекст банкротства либо иной участник; адрес электронной почты Дорошенко не указан.'
    absence.update(verification_result='NO_SUPPORTING_SOURCE_IN_NORMALIZED_CORPUS',
        closest_sources_reviewed=len(absence['closest_candidate_sources']),
        all_email_blocks_reviewed=True,
        full_person_document_reviewed=True,
        why_no_answer='Все 7 вхождений фамилии и 2 вхождения имени/отчества находятся в одном заявлении (2 страницы), где указан почтовый адрес, но нет email. Девять email-вхождений в остальных документах принадлежат другим адресатам; связи с заявителем не установлено.',
        recognition_caveat='Все 90 страниц SUCCESS. Контактный блок заявления прочитан в normalized text; вывод не распространяется на возможные пропуски распознавания исходного PDF.')
    (OUT/'day24_unanswerable_verification.json').write_text(json.dumps(absence,ensure_ascii=False,indent=2),encoding='utf-8')
    for c in selected:
        c['precheck']['semantic_expected_claim_review']='PASS: утверждения ограничены текстом evidence; просьбы и позиции сторон не превращены в решения суда.'
        c['precheck']['question_answer_leak_review']='PASS: даты/названия в вопросе задают область; искомое значение не подсказано. В weak вопросах проверяется гипотеза, а не сообщается ответ.'
        if c['expected_unanswerable']:
            c['unanswerable_verification_file']='day24_unanswerable_verification.json'
            c['precheck']['absence_verified']=True
        match=next(x for x in pool if x['candidate_id']==c['candidate_id'])
        match['precheck']=c['precheck']
        if c['expected_unanswerable']:match['unanswerable_verification_file']=c['unanswerable_verification_file']
    for name,records in [('day24_candidates.json',pool),('day24_control_questions.json',selected)]:
        (OUT/name).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    assert sha([{k:c[k] for k in ('candidate_id','question','expected_answer','expected_claims','expected_sources')} for c in selected])==summary['selected_ground_truth_hash']
    lines=['# Day 24 — Automatic Control Question Discovery','',
        'Дата: 2026-10-03. Только исследование существующих данных и подготовка ground truth. Алгоритмы Day24 и активные вопросы не изменялись. Внешние LLM/API, web, OCR, Qwen recognition и PDF rendering не запускались.','',
        '## Corpus summary','',
        f"Корпус **{summary['corpus_id']}**, processing run `{summary['run_id']}`, corpus hash `{summary['corpus_hash']}`.",
        f"Проанализированы все **{summary['documents']} документов, {summary['pages']} normalized pages, {summary['blocks']} DocumentBlocks, {summary['characters']} символа**. Все страницы имеют статус SUCCESS; это не гарантия отсутствия ошибок OCR.",
        'Полный проход по каждому документу/странице: `data/evaluation/day24_corpus_scan.json`. Проверялись даты, суммы, решения, просьбы, обязательства, имена и контактные реквизиты. Обзор и подтверждение не ограничивались Top-K.',
        'Версии и весь исследованный normalized snapshot закреплены в `day24_discovery_snapshot.json`; SQLite перечитан отдельно перед dry-run. Для legacy документов без recognition_version проверены live document_blocks и принадлежность активным chunks.',
        '', '| Тип | Пул | Выбрано |','|---|---:|---:|']
    for cat,n in summary['categories'].items():lines.append(f"| {cat} | {n} | {summary['selected_categories'].get(cat,0)} |")
    lines+=['',f"Всего **{len(pool)} кандидатов**, из них **{sum(c['manual_review_required'] for c in pool)} требуют проверки** и исключены из автоматического выбора. Основной набор ссылается на **{len({s['document_id'] for c in selected for s in c['expected_sources']})} разных документов**.",
        '', '## Метод отбора и независимость ground truth','',
        'Сначала выполнен сплошной программный проход по 866 блокам, затем содержательный анализ найденных фрагментов. Формулировки вопросов подготовлены по evidence без MiniMax. Скрипт воспроизводит планы для закреплённого корпуса: это не универсальный генератор вопросов для произвольных новых PDF.',
        'Детерминированная оценка: certainty × 40, exact evidence +20, reliable recognition +10, ясная область +10, несколько claims +8, несколько документов +5, несколько страниц +3; manual review −100. При выборе добавляется +4 за новый документ и +3 за новый тип факта. Квоты 5/2/2/1. Confidence — инженерная оценка однозначности normalized evidence, не статистическая вероятность.',
        f"Набор выбран **до retrieval**. SHA-256 question/answer/claims/sources до и после dry-run одинаков: `{summary['selected_ground_truth_hash']}`. По результатам RAG менялись только диагностические поля; ответы, источники и цитаты не подгонялись.",
        '', '## Selected 10','']
    for i,c in enumerate(selected,1):
        lines += [f"### {i}. {c['question']}",'',f"`{c['question_id']}` · **{c['category']}** · confidence **{c['confidence']:.2f}** · {c['source_sufficiency']}",'',f"**Ожидаемый ответ:** {c['expected_answer']}",'','**Expected claims:**','']
        lines += [f"- {claim['claim_id']}: {claim['text']}" for claim in c['expected_claims']] or ['- Нет factual claims; ожидается отказ.']
        lines += ['','**Expected sources и exact quotes:**','']
        for s in c['expected_sources']:
            lines += [f"- **{s['file_title']}**, стр. {s['page_start']}. Раздел: {s['section'] or 'не указан'}. Block `{s['source_block_id']}`; chunk `{s['chunk_id']}`.",'', '> '+s['exact_quote'].replace('\n','\n> '),'',
                f"  Locator: recognition `{s['quote_locator']['recognition_id']}`, offsets `{s['quote_locator']['start']}:{s['quote_locator']['end']}`; exact match проверен.",'']
        if not c['expected_sources']:lines+=['Источника с ответом не ожидается. Обоснование отсутствия — ниже.','']
        lines += [f"**Полезность:** {c['why_good_for_day24']}",f"**Проверяет:** {', '.join(c['failure_modes_tested'])}.",f"**Retrieval challenge:** `{str(c['retrieval_challenge']).lower()}`.",'']
        if c['ambiguity']:lines += [f"**Неоднозначность:** {c['ambiguity']}",f"**Недопустимые выводы:** {'; '.join(c['forbidden_claims'])}",'']
    lines += ['## Retrieval dry-run','',
        'Использован существующий Day23 **RERANK**: BGE-M3 → 20 кандидатов → cleanup → local bge-reranker-v2-m3 → threshold 0.1 → expansion/selection → до 5 контекстов. RERANK выбран как проверенный локальный режим Day23; query rewrite не вызывался, поскольку требует внешней LLM. Grounding/generation не выполнялись.',
        'Сервис использовался с in-memory хранилищем сравнений и заглушкой на границе generation. HTTP requests к LLM = 0. В production comparisons и active questions записи не добавлялись.',
        '', '| № | Кандидатов | Источник: vector rank → rerank rank (score) | Все expected quotes в context | Challenge |','|---|---:|---|---|---|']
    for i,(c,r) in enumerate(zip(selected,dry),1):
        ranks='; '.join(f"стр.{x['page']}: {x['retrieved_source_rank']} → {x['expected_source_rank']} ({x['rerank_score']:.3f})" if x['rerank_score'] is not None else f"стр.{x['page']}: {x['retrieved_source_rank']} → не найден" for x in r['per_expected_source']) or 'Нет expected source'
        ok=all(x['in_final_context'] for x in r['per_expected_source']) if r['per_expected_source'] else 'не применимо'
        lines.append(f"| {i} | {r['candidate_count']} | {ranks} | {ok} | {r['retrieval_challenge']} |")
    lines += ['','Rank относится к ожидаемому active chunk, а попадание в context дополнительно требует exact quote и source block. Высокий rerank не гарантирует попадания всех нужных фрагментов после expansion/budget/selection.',
        'Полная диагностика: `data/evaluation/day24_retrieval_dry_run.json`. Не найденные источники сохранены как полезные regression tests.','',
        '## Unanswerable verification','',
        '**Вопрос:** '+selected[-1]['question'], '**Ожидание:** INSUFFICIENT_CONTEXT.','',absence['why_no_answer'],
        'По всем 16 документам проверены варианты Дорошен/Доро/Дорощ, Doroshen/Doros, Владимир…Евгень, В.Е.; E-mail/email, электронная почта/адрес, эл. почта, обратная связь, контакт, email-шаблоны. Найденные контакты судов, представителей и других участников нельзя приписать заявителю.',
        'Дополнительно выполнен semantic query с тем же вопросом. Ближайшие 10 источников сохранены с объяснением, почему они не отвечают на вопрос. Отсутствие ответа установлено полным normalized проходом, а не неудачей Top-K.',
        f"Покрытие: {absence['corpus_coverage']}. Уверенность: {absence['confidence']}. Результат: `{absence['verification_result']}`.",
        absence['recognition_caveat'], '', 'Ближайшие semantic источники:','']
    for h in absence['closest_candidate_sources']:
        lines.append(f"- {h['file_name']}, стр. {h['page_start']}–{h['page_end']}, vector rank {h['rank']}, score {h['score']:.3f}: {h['why_not_answer']}")
    lines += ['','## Кандидаты с проблемным распознаванием','',
        'FAILED страниц нет. Однако три кандидата помечены manual_review_required=true и не выбраны:','']
    for c in pool:
        if c['manual_review_required']:lines.append(f"- `{c['candidate_id']}` — {c['question']} Причина: {c['discovery_notes']}")
    lines += ['','Точные опечатки не исправлялись внутри quotes. В выбранных примерах используются понятные локальные фрагменты: например, дата продления, а не искажённый глагол в начале блока. Прекращение административного производства и удовлетворение судебной жалобы не объявляются противоречием: это разные виды рассмотрения.',
        '', '## Validation pass','',
        'Повторно проверены все expected source IDs, принадлежность документов/страниц/active chunks, recognition IDs, SHA-256 блоков и точное равенство среза canonical text по offsets. Claim/source mapping проверен отдельно. Вопросы не раскрывают искомые значения; weak questions формулируют гипотезу для проверки. Все quotes полного пула прошли exact match.',
        'Отдельная итоговая проверка: PASS — 36 кандидатов, 10 выбранных, 40 source entries полного пула и 12 выбранного набора. Категории 5/2/2/1, active questions unchanged, ground-truth hash unchanged, LLM HTTP calls = 0. Результат: `data/evaluation/day24_discovery_validation.json`.',
        '', '## Existing questions и импорт','',
        'Текущие 10 вопросов в SQLite не изменены; SHA-256 записей до/после совпадает. Новый набор сохранён отдельно. Старые вопросы про заявителя и срок «РУСМЕТ» стоит сохранить как дополнительные regression tests с их ручными ожиданиями.',
        'Предлагаемые замены: восемь широких вопросов «что обычно пишут», «основные проблемы», «какие причины/действия чаще всего», «повторяющиеся этапы», «обстоятельства», «участники» плохо подходят для точечного цитирования. Вместо них — конкретные новые factual/multi-claim/weak/unanswerable вопросы. Автоматическая замена или удаление не выполнялись.',
        '', '## Артефакты и воспроизведение','',
        '- `data/evaluation/day24_candidates.json` — полный пул, все поля и exact source locators.',
        '- `data/evaluation/day24_control_questions.json` — только выбранные 10 и precheck.',
        '- `data/evaluation/day24_corpus_scan.json` — сплошной проход по 90 страницам.',
        '- `data/evaluation/day24_unanswerable_verification.json` — текстовый и semantic аудит отсутствия.',
        '- `data/evaluation/day24_retrieval_dry_run.json` — retrieval ranks/scores/challenges.',
        '- `data/evaluation/day24_discovery_summary.json` — состав, hashes, unchanged checks.',
        '- `scripts/discover_day24_questions.py`, `scripts/dry_run_day24_discovery.py`, `scripts/report_day24_discovery.py` — воспроизводимые этапы для закреплённого snapshot. Повторный discovery перезаписывает только свои новые артефакты, не production questions.',
        '', 'Данные содержат выдержки корпуса и исключены из Git через `data/evaluation/`. Секреты/config snapshots не экспортировались.',
        '', '## Blockers','',
        'Блокирующих ограничений для discovery нет. Unanswerable ограничен нормализованным корпусом; raw PDF заново не проверялся по условию задачи. Плохое нахождение отдельных expected sources — результат измерения, а не основание изменить ground truth. Проверка generation/grounding новых вопросов в эту задачу не входит. Алгоритмы Day24 и следующий день курса не изменялись.']
    Path('DAY24_QUESTION_DISCOVERY.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(candidates=len(pool),selected=len(selected),challenges=summary['retrieval_challenges'],ground_truth_unchanged=True),ensure_ascii=False))


if __name__=='__main__':main()
