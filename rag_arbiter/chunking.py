import re
from .documents import Chunk, digest


class ModelTokenizer:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def count(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def offsets(self, text):
        return self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]


def source(doc):
    text, spans = "", []
    for block in sorted(doc.blocks, key=lambda b: b.reading_order):
        start = len(text)
        text += ("\n\n" if text else "") + block.text
        spans.append((start, len(text), block))
    return text, spans


def bounded_end(text, start, end, tokenizer, limit):
    """Character slices preserve exact Unicode; never decode partial token sequences."""
    if tokenizer.count(text[start:end]) <= limit:
        return end
    offsets = tokenizer.offsets(text[start:end])
    candidate = start + offsets[min(limit, len(offsets)) - 1][1]
    candidate = max(start + 1, candidate)
    while tokenizer.count(text[start:candidate]) > limit and candidate > start + 1:
        candidate -= 1
    if tokenizer.count(text[start:candidate]) > limit:
        raise ValueError("Token limit cannot fit one Unicode character")
    return candidate


def make_chunks(doc, strategy, ranges, tokenizer, settings):
    text, spans = source(doc)
    chunks = []
    for i, (start, end) in enumerate(ranges):
        blocks = [b for a, z, b in spans if a < end and z > start]
        value = text[start:end]
        chunks.append(Chunk(chunk_id=digest([doc.document_id, doc.cache_identity, strategy, "1", settings, start, end, value]),
            document_id=doc.document_id, strategy=strategy, chunk_index=i, text=value,
            source="uploaded document" if "corpora" in doc.source_path or "uploads" in doc.source_path else "local file",
            file_name=doc.file_name, title=doc.title or doc.file_name,
            token_count=tokenizer.count(value), content_hash=digest(value),
            page_start=min(b.page_number for b in blocks), page_end=max(b.page_number for b in blocks),
            section=" / ".join(dict.fromkeys(b.section for b in blocks if b.section)),
            source_block_ids=[b.block_id for b in blocks], char_start=start, char_end=end,
            recognition_provider=doc.recognition_provider,
            recognition_ids=list(dict.fromkeys(b.recognition_id for b in blocks if b.recognition_id))))
    return chunks


class FixedSizeChunker:
    strategy = "fixed"

    def __init__(self, tokenizer, target_tokens=1000, overlap_tokens=125, max_tokens=1000):
        if not 0 <= overlap_tokens < target_tokens <= max_tokens:
            raise ValueError("Invalid fixed chunk sizes")
        self.tokenizer, self.target, self.overlap, self.maximum = tokenizer, target_tokens, overlap_tokens, max_tokens

    def chunk(self, doc):
        text, _ = source(doc)
        ranges, start = [], 0
        while start < len(text):
            end = bounded_end(text, start, len(text), self.tokenizer, self.target)
            ranges.append((start, end))
            if end == len(text):
                break
            offsets = self.tokenizer.offsets(text[start:end])
            next_start = start + offsets[-self.overlap][0] if self.overlap and len(offsets) > self.overlap else end
            start = next_start if next_start > start else end
        return make_chunks(doc, self.strategy, ranges, self.tokenizer, [self.target, self.overlap, self.maximum])


class StructureAwareChunker:
    strategy = "structure"

    def __init__(self, tokenizer, max_tokens=1000):
        self.tokenizer, self.maximum = tokenizer, max_tokens

    def split(self, text, start, end, level=0):
        if self.tokenizer.count(text[start:end]) <= self.maximum:
            return [(start, end)]
        patterns = [r"\n\n", r"\n", r"(?<=[.!?;])\s+", r"\s+"]
        if level >= len(patterns):
            ranges = []
            while start < end:
                stop = bounded_end(text, start, end, self.tokenizer, self.maximum)
                ranges.append((start, stop))
                start = stop
            return ranges
        boundaries = [start] + [start + m.end() for m in re.finditer(patterns[level], text[start:end])]
        boundaries = sorted(set(boundaries + [end]))
        parts = []
        for a, b in zip(boundaries, boundaries[1:]):
            parts.extend(self.split(text, a, b, level + 1))
        # Recombine smaller sentence/line units without exceeding the token budget.
        merged = []
        for a, b in parts:
            if merged and self.tokenizer.count(text[merged[-1][0]:b]) <= self.maximum:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        return merged

    def chunk(self, doc):
        text, spans = source(doc)
        ranges, pending, previous = [], None, None
        for start, end, block in spans:
            boundary = previous is not None and (
                block.section != previous.section or block.page_number != previous.page_number
                or block.block_type in {"table", "heading", "section_header", "title"} or previous.block_type == "table")
            if pending and (boundary or self.tokenizer.count(text[pending[0]:end]) > self.maximum):
                ranges.extend(self.split(text, *pending))
                pending = None
            pending = (pending[0], end) if pending else (start, end)
            previous = block
        if pending:
            ranges.extend(self.split(text, *pending))
        return make_chunks(doc, self.strategy, ranges, self.tokenizer, [self.maximum])


def coverage(doc, chunks, tokenizer):
    text, _ = source(doc)
    intervals = sorted((c.char_start, c.char_end) for c in chunks)
    end, unique, total = 0, 0, 0
    for c in chunks:
        if c.text != text[c.char_start:c.char_end]:
            raise ValueError("Chunk text differs from source")
    for a, b in intervals:
        if a > end:
            raise ValueError(f"Content lost at character {end}")
        unique += max(0, b - max(end, a))
        total += b - a
        end = max(end, b)
    if end != len(text):
        raise ValueError("Content lost at document end")
    return {"source_characters": len(text), "source_tokens": tokenizer.count(text),
            "chunked_unique_characters": unique, "unique_coverage": unique / len(text) if text else 1,
            "chunked_unique_source_tokens": tokenizer.count(text),
            "overlap_characters": total - unique, "chunk_tokens": sum(c.token_count for c in chunks)}
