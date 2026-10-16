from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class NarrationSegment(BaseModel):
    id: str = Field(description="ID estável, por exemplo seg_0001")
    text: str = Field(min_length=1)
    scene_hint: str = ""
    on_screen_text: str = ""
    source_pages: list[int] = Field(default_factory=list)


class VideoScript(BaseModel):
    title: str
    duration_minutes: int = Field(default=3, gt=0)
    target_audience: str = Field(default="Público geral e leitores")
    hook: str = Field(default="")
    beats: list[str] = Field(min_length=1, description="Blocos narrativos em ordem")
    narration: str = Field(
        min_length=1, description="Texto narrado completo ou suficientemente detalhado"
    )
    segments: list[NarrationSegment] = Field(default_factory=list)
    cta: str = Field(default="")
    b_roll_and_visuals: list[str] = Field(default_factory=list)

    @field_validator("duration_minutes")
    @classmethod
    def supported_duration(cls, value: int) -> int:
        if value not in {1, 3, 20}:
            raise ValueError("Durações suportadas no MVP: 1, 3 ou 20 minutos")
        return value


def ensure_script_segments(
    script: VideoScript,
    source_pages: list[int] | None = None,
) -> VideoScript:
    """Garante que o roteiro possua segmentos narrados estruturados para TTS e legendas."""
    pages = list(source_pages or [])
    if script.segments:
        updated_segments = []
        for idx, seg in enumerate(script.segments, start=1):
            seg_id = seg.id or f"seg_{idx:04d}"
            seg_pages = seg.source_pages or pages
            updated_segments.append(
                seg.model_copy(update={"id": seg_id, "source_pages": seg_pages})
            )
        return script.model_copy(update={"segments": updated_segments})

    raw_paragraphs = [
        p.strip() for p in re.split(r"\n\s*\n+", script.narration.strip()) if p.strip()
    ]
    if not raw_paragraphs:
        raw_paragraphs = [script.narration.strip()]

    segments: list[NarrationSegment] = []
    for idx, paragraph in enumerate(raw_paragraphs, start=1):
        beat_hint = script.beats[(idx - 1) % len(script.beats)] if script.beats else script.title
        visual_hint = (
            script.b_roll_and_visuals[(idx - 1) % len(script.b_roll_and_visuals)]
            if script.b_roll_and_visuals
            else beat_hint
        )
        segments.append(
            NarrationSegment(
                id=f"seg_{idx:04d}",
                text=paragraph,
                scene_hint=visual_hint,
                on_screen_text=beat_hint[:90],
                source_pages=pages,
            )
        )
    return script.model_copy(update={"segments": segments})
