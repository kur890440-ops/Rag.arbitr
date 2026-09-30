import json
import re
import time
from importlib.metadata import version
from .models import PageOutput, ContentOutput, RecognitionResult, RecognizedBlock, RecognizedTable
from .prompt import RECOGNITION_PROMPT, PROMPT_VERSION
from .runtime import OllamaRuntime, RuntimeUnavailable
from ..documents import digest
from ..ocr import DoclingOCRProvider


def repair_json(content):
    """Remove only commas outside strings immediately before a closing delimiter."""
    start, end = content.find('{'), content.rfind('}')
    content = content[start:end+1] if start >= 0 and end >= start else content
    out, quoted, escaped = [], False, False
    for i, char in enumerate(content):
        if quoted:
            out.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        else:
            if char == '"':
                quoted = True
            if char == ',' and content[i+1:].lstrip().startswith(('}', ']')):
                continue
            out.append(char)
    return json.loads(''.join(out))


def normalize_page(output):
    """Use Docling's real document/table model; never invent VLM bounding boxes."""
    from docling_core.types.doc import DoclingDocument, DocItemLabel, TableData, TableCell
    doc = DoclingDocument(name=f"page-{output.page_number}")
    diagnostics = []
    labels = {"title": DocItemLabel.TITLE, "heading": DocItemLabel.SECTION_HEADER,
              "paragraph": DocItemLabel.TEXT, "list": DocItemLabel.LIST_ITEM,
              "caption": DocItemLabel.CAPTION, "other": DocItemLabel.TEXT}
    # Some small VLMs repeat table rows as table-typed text blocks even with a schema.
    # Remove only exact textual duplicates of structured rows/cells; never discard unmatched content.
    def whitespace(text):
        return " ".join(text.split())
    row_texts = {(table.order + i, whitespace(" ".join(row))) for table in output.tables for i, row in enumerate(table.rows)}
    table_texts = {(table.order, whitespace(" ".join(cell for row in table.rows for cell in row))) for table in output.tables if table.rows}
    retained = []
    for block in output.blocks:
        if block.type == "table" and (block.order, whitespace(block.text)) in row_texts | table_texts:
            diagnostics.append(f"Exact duplicate table text block at order {block.order} removed; structured cells preserved")
        else:
            retained.append(block)
    entries = sorted([*retained, *output.tables], key=lambda b: b.order)
    if len({b.order for b in entries}) != len(entries):
        groups = {}
        for item in entries:
            groups.setdefault(item.order, []).append(item)
        for order, group in groups.items():
            if len(group) == 1:
                continue
            # A text representation and structured table may share a position.
            # Preserve unmatched text rather than silently treating it as a duplicate.
            if len(group) != 2 or sum(isinstance(item, RecognizedTable) for item in group) != 1 or not all(
                isinstance(item, RecognizedTable) or item.type == "table" for item in group
            ):
                # VLM geometry is unverified. Preserve the original block array,
                # then the legacy table array; do not invent interleaving.
                entries = [*retained, *output.tables]
                for position, item in enumerate(entries):
                    item.order = position
                    item.uncertain = True
                diagnostics.append("UNCERTAIN: conflicting order; original VLM sequence preserved; legacy tables appended")
                break
            for item in group:
                item.uncertain = True
                if isinstance(item, RecognizedBlock):
                    item.type = "other"
            diagnostics.append(f"Ambiguous table/text at order {order}: both preserved; reading order renumbered")
        for order, item in enumerate(entries):
            item.order = order
    blocks = []
    for item in entries:
        if item.bbox is not None:
            diagnostics.append("Unverified VLM bbox discarded")
        item.bbox = None
        if isinstance(item, RecognizedTable):
            if item.rows:
                width = max(map(len, item.rows))
                cells = [TableCell(text=text, start_row_offset_idx=r, end_row_offset_idx=r+1,
                           start_col_offset_idx=c, end_col_offset_idx=c+1) for r, row in enumerate(item.rows) for c, text in enumerate(row)]
                table = doc.add_table(data=TableData(num_rows=len(item.rows), num_cols=width, table_cells=cells))
                item.markdown = table.export_to_markdown(doc=doc)
            elif item.markdown:
                # Preserve Markdown-only fallback verbatim in the Docling model.
                doc.add_text(label=DocItemLabel.TEXT, text=item.markdown)
            else:
                raise ValueError("Empty table has neither rows nor Markdown")
            blocks.append(RecognizedBlock(type="table", text=item.markdown, order=item.order, uncertain=item.uncertain))
        else:
            if item.type not in labels and item.type != "table":
                diagnostics.append(f"Unknown block type {item.type!r} normalized to other")
                item.type = "other"
            doc.add_text(label=labels.get(item.type, DocItemLabel.TEXT), text=item.text)
            blocks.append(item)
    return blocks, doc.export_to_dict(), diagnostics


class Qwen3VLRecognitionProvider:
    def __init__(self, config, runtime=None, fallback=None):
        self.config = config
        self.runtime = runtime or OllamaRuntime(config.recognition)
        self.info = None
        self.fallback = fallback

    def preflight(self):
        try:
            self.info = self.runtime.preflight()
        except RuntimeUnavailable as exc:
            if not self.config.recognition.fallback_enabled:
                raise
            self.info = dict(model=self.config.recognition.model, model_version='unavailable',
                             runtime='ollama', runtime_version='unavailable', quantization=self.config.recognition.quantization,
                             effective_device='unavailable', error=str(exc))
        return self.info

    def identity(self):
        info = self.info or self.preflight()
        settings = self.config.recognition.model_dump()
        if not settings['split_after_first_truncation']:
            settings.pop('split_after_first_truncation')  # Keep existing cache identities unchanged.
        return {"provider": "qwen3_vl", "model": info["model"], "model_version": info["model_version"],
                "runtime": info["runtime"], "runtime_version": info["runtime_version"],
                "quantization": info["quantization"], "settings": settings,
                "prompt_hash": digest([PROMPT_VERSION, RECOGNITION_PROMPT, ContentOutput.model_json_schema()]),
                "parser": version("docling"), "core": version("docling-core"), "normalizer": "recognition-4"}

    def recognize_page(self, request):
        from .recovery import recover_page
        return recover_page(self, request)

    def recognize_once(self, request):
        identity = self.identity()
        start = time.perf_counter()
        result = RecognitionResult(provider="qwen3_vl", model=identity["model"], model_version=identity["model_version"],
            runtime=identity["runtime"], device=self.info.get("effective_device", "unknown"),
            quantization=identity["quantization"], page_number=request.page_number, raw_output="")
        failure_scope = "runtime"
        try:
            prompt = RECOGNITION_PROMPT + f"\nlanguage hints={request.language_hints}"
            response = self.runtime.generate(request, prompt, ContentOutput.model_json_schema())
            failure_scope = "page"
            result.raw_output = response.raw  # Keep the entire wire response even when parsing fails.
            result.device, result.diagnostics = response.device, response.diagnostics
            if response.diagnostics.get("done_reason") == "length" or response.diagnostics.get("done") is False:
                raise ValueError("Generation truncated; increase generation/context budget or inspect page")
            content = response.content.strip()
            if content.startswith("```"):
                match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
                if match:
                    content = match[1]
            repaired_json = False
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                # Only a complete JSON object and trailing commas can be repaired.
                # Never manufacture closing delimiters for truncated content.
                data = repair_json(content)
                repaired_json = True
            if 'page_number' in data and data['page_number'] != request.page_number:
                raise ValueError("VLM returned a different page_number")
            data['page_number'] = request.page_number
            data.setdefault('tables', [])
            for i, block in enumerate(data.get('blocks', [])):
                block.setdefault('order', i)
            output = PageOutput.model_validate(data)
            if output.page_number != request.page_number:
                raise ValueError("VLM returned a different page_number")
            result.blocks, result.docling_document, warnings = normalize_page(output)
            result.tables = output.tables
            result.normalized_text = "\n\n".join(b.text for b in result.blocks)
            result.reading_order = [b.order for b in result.blocks]
            result.diagnostics["normalization"] = warnings
            result.diagnostics['reading_order_status'] = 'UNCERTAIN' if any('UNCERTAIN' in w or 'Ambiguous' in w for w in warnings) else 'REPAIRED' if warnings or repaired_json else 'OK'
            result.diagnostics['json_repaired'] = repaired_json
            if not result.normalized_text.strip():
                result.status = "EMPTY"
            elif any(b.uncertain or "[UNREADABLE]" in b.text for b in result.blocks):
                result.status = "UNCERTAIN"
        except Exception as exc:
            result.status = "FAILED"
            result.diagnostics["error"] = str(exc)
            result.diagnostics["failure_scope"] = failure_scope
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result

    def close(self):
        self.runtime.close()
        if self.fallback:
            self.fallback.close()


class ClassicOCRRecognitionProvider:
    """Adapter: retains existing OCRProvider/Docling implementation unchanged."""
    def __init__(self, config, ocr=None):
        self.config, self.ocr = config, ocr or DoclingOCRProvider(config)

    def preflight(self):
        identity = self.identity()
        return {**identity, "effective_device": "cpu", "note": "RapidOCR ONNX CPU; Docling layout device managed by Docling"}

    def identity(self):
        original = self.ocr.identity()
        return {"provider": "classic_ocr", "model": "RapidOCR/eslav-PP-OCRv5", "model_version": original["version"],
                "runtime": "docling/onnxruntime", "runtime_version": original.get("onnxruntime", "test"),
                "quantization": "native", "parser": original["parser"], "ocr": original, "normalizer": "recognition-1"}

    def recognize_page(self, request):
        identity = self.identity()
        start = time.perf_counter()
        result = RecognitionResult(provider="classic_ocr", model=identity["model"], model_version=identity["model_version"],
            runtime=identity["runtime"], device="cpu", quantization="native", page_number=request.page_number, raw_output="")
        try:
            doc = self.ocr.convert_page(request.image_path)
            result.docling_document = doc.export_to_dict()
            result.raw_output = json.dumps(result.docling_document, ensure_ascii=False)
            label_map = {"section_header": "heading", "text": "paragraph", "list_item": "list"}
            for item, _ in doc.iterate_items():
                kind = label_map.get(item.label.value, item.label.value)
                text = item.export_to_markdown(doc=doc) if kind == "table" else getattr(item, "text", "")
                if not text.strip():
                    continue
                order = len(result.blocks)
                bbox = item.prov[0].bbox.model_dump(mode="json") if item.prov else None
                if kind not in {"title", "heading", "paragraph", "list", "table", "caption", "other"}:
                    kind = "other"
                result.blocks.append(RecognizedBlock(type=kind, text=text, order=order, bbox=bbox))
                if kind == "table":
                    result.tables.append(RecognizedTable(order=order, markdown=text, bbox=bbox))
            result.normalized_text = "\n\n".join(b.text for b in result.blocks)
            result.reading_order = [b.order for b in result.blocks]
            if not result.normalized_text:
                result.status = "EMPTY"
        except Exception as exc:
            result.status = "FAILED"
            result.diagnostics["error"] = str(exc)
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result

    def close(self):
        pass
