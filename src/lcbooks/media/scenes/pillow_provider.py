from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lcbooks.exceptions import SceneGenerationError
from lcbooks.schemas.video import StoryboardScene


class PillowSceneProvider:
    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        font_path: str | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.font_path = font_path

    def _font(self, size: int):
        if self.font_path:
            try:
                return ImageFont.truetype(self.font_path, size=size)
            except OSError as exc:
                raise SceneGenerationError(
                    f"Não foi possível carregar a fonte configurada: {self.font_path}",
                    retryable=False,
                ) from exc
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for candidate in candidates:
            if Path(candidate).is_file():
                try:
                    return ImageFont.truetype(candidate, size=size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def create_scene(self, scene: StoryboardScene, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (self.width, self.height), (18, 25, 48))
        draw = ImageDraw.Draw(image)

        draw.ellipse(
            (
                self.width * 0.68,
                -self.height * 0.28,
                self.width * 1.08,
                self.height * 0.42,
            ),
            fill=(38, 76, 122),
        )
        draw.rounded_rectangle(
            (
                int(self.width * 0.07),
                int(self.height * 0.12),
                int(self.width * 0.93),
                int(self.height * 0.86),
            ),
            radius=36,
            fill=(25, 36, 65),
            outline=(85, 120, 165),
            width=3,
        )
        draw.text(
            (int(self.width * 0.12), int(self.height * 0.18)),
            scene.visual_type.upper(),
            font=self._font(max(18, self.height // 40)),
            fill=(139, 202, 255),
        )

        title = (scene.on_screen_text or scene.visual_prompt or scene.scene_id).strip()
        wrap_width = 24 if self.height > self.width else 32
        lines = textwrap.wrap(title, width=wrap_width)[:5]
        font = self._font(max(24, min(self.width, self.height) // 16))
        y = int(self.height * 0.34)
        for line in lines:
            draw.text(
                (int(self.width * 0.12), y),
                line,
                font=font,
                fill=(248, 250, 255),
            )
            y += int(self.height * 0.08)

        footer = "LCBOOKS"
        if scene.source_pages:
            pages_label = (
                f"Pág. {scene.source_pages[0]}-{scene.source_pages[-1]}"
                if len(scene.source_pages) > 1
                else f"Pág. {scene.source_pages[0]}"
            )
            footer = f"LCBOOKS • {pages_label}"
        draw.text(
            (int(self.width * 0.12), int(self.height * 0.78)),
            footer,
            font=self._font(max(14, self.height // 65)),
            fill=(170, 185, 210),
        )

        temp_path = output_path.with_suffix(".tmp.png")
        image.save(temp_path, format="PNG", optimize=True)
        with Image.open(temp_path) as verify_img:
            if verify_img.size != (self.width, self.height):
                temp_path.unlink(missing_ok=True)
                raise SceneGenerationError(
                    f"Dimensões inválidas na cena gerada: {verify_img.size}",
                    retryable=False,
                )
        temp_path.replace(output_path)
        return output_path
