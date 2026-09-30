import re
import shutil
from pathlib import Path
from uuid import uuid4
from PIL import Image
from ..documents import file_hash, now, digest

TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def safe_filename(name):
    if not name or len(name) > 180 or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
        raise ValueError("Недопустимое имя файла / path traversal")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}:
        raise ValueError("Недопустимое имя файла Windows")
    if Path(name).suffix.lower() not in TYPES:
        raise ValueError("Допустимы только PDF, PNG, JPG, JPEG, WEBP")
    return name


def contained(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("File outside allowed project directory")
    return path


class UploadService:
    def __init__(self, runs):
        self.runs, self.config = runs, runs.config

    def save(self, filename, content_type, stream):
        filename = safe_filename(filename)
        extension = Path(filename).suffix.lower()
        if content_type not in {TYPES[extension], "application/octet-stream"}:
            raise ValueError("MIME type не соответствует расширению")
        upload_id = uuid4().hex
        folder = self.config.uploads_path / upload_id
        folder.mkdir(parents=True, exist_ok=False)
        path = contained(folder / filename, self.config.uploads_path)
        size = 0
        try:
            with path.open("wb") as output:
                while part := stream.read(1024 * 1024):
                    size += len(part)
                    if self.config.max_upload_size and size > self.config.max_upload_size:
                        raise ValueError("Превышен max_upload_size")
                    output.write(part)
            if not size:
                raise ValueError("Пустой файл")
            if extension == ".pdf":
                import pypdfium2 as pdfium
                with path.open("rb") as f:
                    if not f.read(1024).lstrip().startswith(b"%PDF-"):
                        raise ValueError("Content signature не соответствует PDF")
                with pdfium.PdfDocument(path) as doc:
                    pages = len(doc)
                    if pages < 1:
                        raise ValueError("PDF не содержит страниц")
            else:
                with Image.open(path) as image:
                    if image.format != {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[extension]:
                        raise ValueError("Image signature не соответствует расширению")
                    image.verify()
                pages = 1
            record = {"upload_id": upload_id, "filename": filename, "path": str(path.resolve()),
                      "content_type": TYPES[extension], "size": size, "pages": pages, "content_hash": file_hash(path), "created_at": now()}
            with self.runs.db() as store:
                store.put("uploads", upload_id, record)
            return {k: v for k, v in record.items() if k != "path"}
        except Exception as exc:
            path.unlink(missing_ok=True)
            folder.rmdir()
            if isinstance(exc, ValueError):
                raise
            raise ValueError("Файл повреждён или не поддерживается") from exc

    def run_config(self, upload_ids, provider=None):
        if not upload_ids or (self.config.max_upload_files and len(upload_ids) > self.config.max_upload_files) or len(set(upload_ids)) != len(upload_ids):
            raise ValueError("Выберите допустимое количество уникальных файлов")
        with self.runs.db() as store:
            records = [store.get("uploads", i) for i in upload_ids]
        if not all(records):
            raise ValueError("Неизвестный upload ID")
        corpus = self.config.web_data_path / "corpora" / digest(sorted(upload_ids))
        for record in records:
            source = contained(record["path"], self.config.uploads_path)
            if file_hash(source) != record["content_hash"]:
                raise ValueError("Uploaded original изменён после проверки")
            target = corpus / record["upload_id"] / record["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(source, target)
            elif file_hash(target) != record["content_hash"]:
                raise ValueError("Run corpus изменён; загрузите оригинал заново")
        # Stable corpus location permits recognition reuse on repeated processing of the same selection.
        run_folder = self.config.web_data_path / "runs" / uuid4().hex
        run_folder.mkdir(parents=True)
        cfg = self.config.model_copy(deep=True)
        cfg.corpus_path, cfg.manifest_path = corpus, corpus / "manifest.json"
        cfg.snapshot_path, cfg.report_path = run_folder / "snapshot.json", run_folder / "report.html"
        if provider:
            if provider not in {"qwen3_vl", "classic_ocr"}:
                raise ValueError("Unknown recognition provider")
            cfg.recognition.provider = provider
        return cfg
