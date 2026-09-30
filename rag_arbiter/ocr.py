from typing import Protocol
from importlib.metadata import version


class OCRProvider(Protocol):
    def identity(self) -> dict: ...
    def convert_page(self, image_path): ...


class DoclingOCRProvider:
    def __init__(self, config):
        self.config = config
        self.converter = None

    def identity(self):
        return {"parser": version("docling"), "core": version("docling-core"),
                "backend": "rapidocr", "version": version("rapidocr"),
                "languages": self.config.ocr_languages, "dpi": self.config.render_dpi,
                "onnxruntime": version("onnxruntime"), "models": version("docling-ibm-models"),
                "remote_services": False, "table_structure": True,
                "mode": "full_page", "normalizer": "1"}

    def convert_page(self, image_path):
        if self.converter is None:
            from docling.document_converter import DocumentConverter, ImageFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, OcrMode
            options = PdfPipelineOptions(do_ocr=True, do_table_structure=True,
                                         enable_remote_services=False)
            options.ocr_options = RapidOcrOptions(lang=self.config.ocr_languages,
                                                 mode=OcrMode.FULL_PAGE, backend="onnxruntime")
            self.converter = DocumentConverter(allowed_formats=[InputFormat.IMAGE],
                format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=options)})
        result = self.converter.convert(image_path, raises_on_error=True)
        if result.status.value != "success":
            raise RuntimeError(f"Docling: {result.status}; {result.errors}")
        return result.document
