from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    gemini_api_key: str | None = Field(default=None, repr=False)
    google_api_key: str | None = Field(default=None, repr=False)
    gemini_model: str = "gemini-2.5-flash"

    tts_provider: Literal["google_cloud", "fake"] = "google_cloud"
    tts_language: str = "pt-BR"
    tts_voice: str = "pt-BR-Standard-A"
    tts_speaking_rate: float = Field(default=1.0, ge=0.25, le=4.0)

    video_width: int = Field(default=1920, ge=320, le=3840)
    video_height: int = Field(default=1080, ge=320, le=3840)
    video_fps: int = Field(default=30, ge=15, le=60)
    video_crf: int = Field(default=20, ge=16, le=30)
    video_preset: str = "medium"
    burn_captions: bool = True
    lcbooks_font_path: str | None = None

    max_pdf_mb: int = Field(default=100, ge=1, le=1000)
    max_pdf_pages: int = Field(default=1000, ge=1, le=10000)
    max_chapter_chars_per_chunk: int = Field(default=12000, ge=2000, le=50000)
    max_retry_attempts: int = Field(default=3, ge=0, le=8)

    def safe_dict(self) -> dict[str, Any]:
        """Retorna configuração sanitizada sem chaves de API."""
        return self.model_dump(exclude={"gemini_api_key", "google_api_key"})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
