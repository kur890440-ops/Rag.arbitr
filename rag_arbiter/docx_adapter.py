"""Docling DOCX adapter into the existing canonical Document/Block model.

DOCX has no physical page coordinates. page_number=0 is an internal sentinel,
not a page: locator_kind='structure', source_ref and block_id are authoritative.
"""
from importlib.metadata import version
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from .documents import Document, Block, digest, file_hash, save_json

MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def validate_docx(path):
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(e.file_size for e in entries) > 256*1024*1024:
                raise ValueError('DOCX archive exceeds expansion limits')
            if not {'[Content_Types].xml', 'word/document.xml'} <= set(archive.namelist()):
                raise ValueError('Invalid DOCX package')
            if any(e.flag_bits & 1 for e in entries):
                raise ValueError('Encrypted DOCX is unsupported')
    except BadZipFile as exc:
        raise ValueError('Invalid DOCX package') from exc


def normalize_docx(path, config, store, reporter, source_path=None, conversion_identity=None):
    validate_docx(path)
    original = source_path or path
    content_hash = file_hash(original)
    did = digest([str(original.resolve()), content_hash])
    identity = {'provider':'docling_docx','parser':version('docling'), 'core':version('docling-core'), 'adapter':'docx-1'}
    if conversion_identity:
        identity['conversion'] = conversion_identity
    key = digest([did, identity])
    cached = store.get('normalized_documents', key)
    if cached and cached.get('status') == 'SUCCESS':
        return Document(**cached), True
    from docling.document_converter import DocumentConverter
    from docling.datamodel.base_models import InputFormat
    # DOCX uses Docling's native Office parser, no OCR/model/runtime or network.
    result = DocumentConverter(allowed_formats=[InputFormat.DOCX]).convert(path, raises_on_error=True)
    if result.status.value != 'success':
        raise ValueError('DOCX conversion failed')
    parsed, blocks, heading, table_depth = result.document, [], '', None
    for item, level in parsed.iterate_items():
        reporter.check()
        if table_depth is not None:
            if level > table_depth:
                continue  # Cells belong to the table, never duplicate them as paragraphs.
            table_depth = None
        kind = item.label.value
        table_data = None
        if kind == 'table':
            text = item.export_to_markdown(doc=parsed)
            table_data = item.data.model_dump(mode='json')
            table_depth = level
        else:
            text = getattr(item, 'text', '')
        if not text.strip():
            continue
        if kind in {'section_header', 'title'}:
            heading = text
        blocks.append(Block(block_id=digest([did,key,item.self_ref,text]), document_id=did,
            page_number=0, locator_kind='structure', source_ref=item.self_ref, table_data=table_data,
            block_type=kind, text=text, reading_order=len(blocks), section=heading,
            content_hash=digest(text), recognition_id=key))
    if not blocks:
        raise ValueError('DOCX_EMPTY')
    doc = Document(document_id=did, source_path=str(original.resolve()), file_name=original.name, title=original.stem,
        content_type='application/msword' if conversion_identity else MIME, content_hash=content_hash, page_count=0, locator_kind='structure',
        parser_version=identity['parser'], ocr_engine='none', recognition_provider='docling_docx',
        blocks=blocks, cache_identity=key)
    raw = config.cache_path/'docx'/f'{key}.json'
    save_json(raw, parsed.export_to_dict())
    store.put('recognition_metadata',key,dict(recognition_id=key, document_id=did,
        provider='docling_docx', model='native-docx', runtime='docling', device='cpu',
        status='SUCCESS', locator_kind='structure', page_number=0, duration_ms=0,
        raw_output_path=str(raw.resolve()), identity=identity))
    store.document(doc)
    save_json(config.cache_path/'parsed'/f'{key}.json',doc.model_dump())
    return doc, False
