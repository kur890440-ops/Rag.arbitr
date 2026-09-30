import re
import pytest
import numpy as np
from rag_arbiter.config import Config
from rag_arbiter.documents import Block, Document, digest


class WordTokenizer:
    def count(self, text):
        return len(self.offsets(text))

    def offsets(self, text):
        return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]


class FakeEmbedding:
    dimension = 8
    tokenizer = WordTokenizer()
    identity = {"model": "TEST_ONLY", "revision": "1", "dimension": 8, "normalization": "L2"}

    def encode(self, texts):
        result = []
        for text in texts:
            v = np.array([int(digest(text)[i:i + 2], 16) + 1 for i in range(0, 16, 2)], dtype=np.float32)
            result.append(v / np.linalg.norm(v))
        return np.array(result)


@pytest.fixture
def config(tmp_path):
    cfg = Config(corpus_path=tmp_path / "corpus", cache_path=tmp_path / "cache",
        sqlite_path=tmp_path / "sqlite" / "metadata.db", qdrant_path=tmp_path / "qdrant",
        report_path=tmp_path / "reports" / "day21.html", manifest_path=tmp_path / "manifest.json",
        queries_path=tmp_path / "queries.json", web_data_path=tmp_path / "web", uploads_path=tmp_path / "uploads", fixed_tokens=8, fixed_overlap=2, fixed_max_tokens=8,
        structure_max_tokens=8, batch_size=2)
    cfg.prepare()
    # Unit tests inject runtimes; never load a real OCR fallback implicitly.
    cfg.recognition.fallback_enabled = False
    cfg.recognition.recovery_retries = 0
    cfg.recognition.split_enabled = False
    return cfg


@pytest.fixture
def doc():
    entries = [(1, "section_header", "Раздел первый", "Раздел первый"),
               (1, "text", "один два три четыре пять шесть семь восемь девять десять", "Раздел первый"),
               (2, "table", "| Товар | Цена |\n| Хлеб | 100 |", "Раздел первый"),
               (2, "section_header", "Раздел второй", "Раздел второй"),
               (2, "list_item", "Первый пункт списка", "Раздел второй")]
    blocks = [Block(block_id=str(i), document_id="doc", page_number=p, block_type=t, text=text,
        reading_order=i, section=s, content_hash=digest(text)) for i, (p, t, text, s) in enumerate(entries)]
    return Document(document_id="doc", source_path="fixture.pdf", file_name="fixture.pdf", title="fixture",
        content_hash="hash", page_count=2, parser_version="test", ocr_engine="test", blocks=blocks)


def make_scanned_pdf(path, pages=1):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
    image = Image.new("RGB", (1200, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 80), "ТЕСТОВЫЙ ДОКУМЕНТ", font=font, fill="black")
    draw.text((80, 180), "Арбитражный суд. Договор поставки.", font=font, fill="black")
    draw.text((80, 260), "Срок оплаты составляет десять дней.", font=font, fill="black")
    for y, row in [(400, "Товар              Цена"), (480, "Хлеб               100"), (560, "Молоко             200")]:
        draw.text((100, y), row, font=font, fill="black")
        draw.line((80, y - 10, 900, y - 10), fill="black", width=2)
    pdf = canvas.Canvas(str(path), pagesize=(600, 700))
    for _ in range(pages):
        pdf.drawImage(ImageReader(image), 0, 0, 600, 700)
        # Deliberately misleading invisible embedded text: OCR must not consume this.
        hidden = pdf.beginText(20, 20)
        hidden.setTextRenderMode(3)
        hidden.textLine("HIDDEN_LAYER_POISON")
        pdf.drawText(hidden)
        pdf.showPage()
    pdf.save()
    image.close()
