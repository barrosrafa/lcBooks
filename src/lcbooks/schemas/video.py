from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VideoStatus = Literal[
    "pending",
    "script_ready",
    "storyboard_ready",
    "assets_ready",
    "audio_ready",
    "captions_ready",
    "rendering",
    "rendered",
    "quality_checked",
    "completed",
    "failed",
]


class StoryboardScene(BaseModel):
    scene_id: str
    segment_ids: list[str] = Field(min_length=1)
    visual_type: Literal["title", "image", "diagram", "quote", "chapter_card"]
    visual_prompt: str = ""
    on_screen_text: str = ""
    asset_path: str | None = None
    duration_seconds: float = Field(default=5.0, gt=0)
    source_pages: list[int] = Field(default_factory=list)


class QualityReport(BaseModel):
    passed: bool
    video_codec: str = ""
    audio_codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration_seconds: float = 0.0
    has_audio: bool = False
    captions_present: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class VideoManifest(BaseModel):
    job_id: str
    chapter_number: int
    script_duration_minutes: int
    status: VideoStatus = "pending"
    script_path: str
    storyboard_path: str | None = None
    narration_path: str | None = None
    captions_path: str | None = None
    video_path: str | None = None
    quality_report_path: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    attempts: int = 0
    failed_stage: str | None = None
    error_message: str | None = None
    retryable: bool = False
    script_hash: str | None = None
    config_hash: str | None = None
    prompt_version: str | None = None
