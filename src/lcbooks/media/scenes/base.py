from __future__ import annotations

from pathlib import Path
from typing import Protocol

from lcbooks.schemas.video import StoryboardScene


class SceneAssetProvider(Protocol):
    def create_scene(self, scene: StoryboardScene, output_path: Path) -> Path: ...
