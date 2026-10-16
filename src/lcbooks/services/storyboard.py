from __future__ import annotations

from typing import Literal

from lcbooks.schemas.chapters import ChapterBoundary
from lcbooks.schemas.script import VideoScript, ensure_script_segments
from lcbooks.schemas.video import StoryboardScene

VisualType = Literal["title", "image", "diagram", "quote", "chapter_card"]


class StoryboardGenerator:
    """Mapeia os segmentos narrados de um roteiro em cenas visuais estruturadas."""

    def __init__(self, *, words_per_minute: float = 140.0) -> None:
        self.words_per_minute = words_per_minute

    def _estimate_seconds(self, text: str) -> float:
        words = max(1, len((text or "").split()))
        seconds = (words / max(60.0, self.words_per_minute)) * 60.0
        return round(max(2.0, seconds), 2)

    @staticmethod
    def _pick_visual_type(index: int, total: int, text: str) -> VisualType:
        if index == 1:
            return "chapter_card"
        if index == total and total > 2:
            return "title"
        if '"' in text or "'" in text:
            return "quote"
        return "image"

    def create(
        self,
        script: VideoScript,
        boundary: ChapterBoundary | None = None,
    ) -> list[StoryboardScene]:
        normalized = ensure_script_segments(
            script,
            source_pages=(
                list(range(boundary.start_page, boundary.end_page + 1))
                if boundary is not None
                else []
            ),
        )
        segments = normalized.segments
        total = len(segments)
        scenes: list[StoryboardScene] = []

        for idx, segment in enumerate(segments, start=1):
            vtype = self._pick_visual_type(idx, total, segment.text)
            if idx == 1 and boundary is not None:
                on_screen = f"Capítulo {boundary.number}: {boundary.title}"
            else:
                on_screen = segment.on_screen_text or (
                    script.beats[(idx - 1) % len(script.beats)] if script.beats else script.title
                )
            prompt = segment.scene_hint or (
                script.b_roll_and_visuals[(idx - 1) % len(script.b_roll_and_visuals)]
                if script.b_roll_and_visuals
                else on_screen
            )
            scenes.append(
                StoryboardScene(
                    scene_id=f"scene_{idx:03d}",
                    segment_ids=[segment.id],
                    visual_type=vtype,
                    visual_prompt=prompt,
                    on_screen_text=on_screen[:120],
                    duration_seconds=self._estimate_seconds(segment.text),
                    source_pages=segment.source_pages,
                )
            )
        return scenes
