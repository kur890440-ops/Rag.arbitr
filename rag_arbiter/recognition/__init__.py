from .models import DocumentRecognitionProvider, RecognitionRequest, RecognitionResult, VisionModelRuntime
from .providers import ClassicOCRRecognitionProvider, Qwen3VLRecognitionProvider


def create_provider(config):
    if config.recognition.provider == "classic_ocr":
        return ClassicOCRRecognitionProvider(config)
    return Qwen3VLRecognitionProvider(config)
