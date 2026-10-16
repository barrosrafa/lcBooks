from __future__ import annotations


class LCBooksError(Exception):
    """Exceção base de domínio do LCBooks classificada por estágio."""

    stage: str = "general"

    def __init__(self, message: str, *, stage: str | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        if stage is not None:
            self.stage = stage
        self.retryable = retryable


class PdfExtractionError(LCBooksError):
    stage = "pdf_extraction"


class ChapterDetectionError(LCBooksError):
    stage = "chapter_detection"


class LLMServiceError(LCBooksError):
    stage = "llm"


class ScriptValidationError(LCBooksError):
    stage = "script_generation"


class StoryboardError(LCBooksError):
    stage = "storyboard"


class SceneGenerationError(LCBooksError):
    stage = "assets"


class TTSServiceError(LCBooksError):
    stage = "audio"


class CaptionError(LCBooksError):
    stage = "captions"


class VideoRenderError(LCBooksError):
    stage = "rendering"


class VideoQualityError(LCBooksError):
    stage = "quality_check"
