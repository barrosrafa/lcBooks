from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lcbooks.exceptions import LCBooksError
from lcbooks.llm.prompts import PROMPT_VERSION
from lcbooks.logging_config import sanitize_message
from lcbooks.schemas.chapters import (
    ChapterBoundary,
    ChapterMap,
    ChapterPackage,
    ChapterSummary,
)
from lcbooks.schemas.script import VideoScript
from lcbooks.schemas.video import QualityReport, StoryboardScene, VideoManifest, VideoStatus


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content, encoding=encoding)
    tmp_path.replace(path)
    return path


def atomic_write_json(path: Path, data: Any) -> Path:
    serialized = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return atomic_write_text(path, serialized)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactStore:
    """Gerencia escrita atômica, caminhos relativos, hashes e manifestos."""

    def __init__(self, base_dir: Path, *, job_id: str = "job_default") -> None:
        self.base_dir = base_dir
        self.job_id = job_id
        self._failures: list[dict[str, Any]] = []

    def relative_str(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.base_dir.resolve()).as_posix()
        except ValueError:
            return path.name

    def chapter_dir(self, chapter_number: int, output_dir: Path | None = None) -> Path:
        root = output_dir or self.base_dir
        return root / f"chapter_{chapter_number:02d}"

    def script_path(
        self, chapter_number: int, duration_minutes: int, output_dir: Path | None = None
    ) -> Path:
        return (
            self.chapter_dir(chapter_number, output_dir) / f"script_{duration_minutes:02d}min.json"
        )

    def video_dir(
        self, chapter_number: int, duration_minutes: int, output_dir: Path | None = None
    ) -> Path:
        return self.chapter_dir(chapter_number, output_dir) / f"video_{duration_minutes:02d}min"

    def video_manifest_path(
        self, chapter_number: int, duration_minutes: int, output_dir: Path | None = None
    ) -> Path:
        return self.video_dir(chapter_number, duration_minutes, output_dir) / "manifest.json"

    def save_book_metadata(
        self, pdf_path: Path, page_count: int, output_dir: Path | None = None
    ) -> Path:
        root = output_dir or self.base_dir
        payload = {
            "job_id": self.job_id,
            "pdf_name": pdf_path.name,
            "pdf_sha256": sha256_file(pdf_path) if pdf_path.is_file() else "",
            "page_count": page_count,
            "prompt_version": PROMPT_VERSION,
        }
        return atomic_write_json(root / "book.json", payload)

    def save_chapter_map(
        self,
        mapping_or_boundaries: ChapterMap | list[ChapterBoundary],
        output_dir: Path | None = None,
    ) -> Path:
        root = output_dir or self.base_dir
        if isinstance(mapping_or_boundaries, ChapterMap):
            payload = mapping_or_boundaries.model_dump()
        else:
            payload = ChapterMap(chapters=list(mapping_or_boundaries), notes="").model_dump()
        return atomic_write_json(root / "mapa_capitulos.json", payload)

    def save_summary(
        self,
        boundary: ChapterBoundary,
        summary: ChapterSummary,
        output_dir: Path | None = None,
    ) -> Path:
        ch_dir = self.chapter_dir(boundary.number, output_dir)
        atomic_write_json(ch_dir / "chapter.json", boundary.model_dump())
        return atomic_write_json(ch_dir / "summary.json", summary.model_dump())

    def load_summary(
        self, chapter_number: int, output_dir: Path | None = None
    ) -> ChapterSummary | None:
        path = self.chapter_dir(chapter_number, output_dir) / "summary.json"
        if not path.is_file():
            return None
        try:
            return ChapterSummary.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_script(
        self,
        boundary: ChapterBoundary,
        script: VideoScript,
        output_dir: Path | None = None,
    ) -> Path:
        path = self.script_path(boundary.number, script.duration_minutes, output_dir)
        return atomic_write_json(path, script.model_dump())

    def load_script(
        self,
        chapter_number: int,
        duration_minutes: int,
        output_dir: Path | None = None,
    ) -> VideoScript | None:
        path = self.script_path(chapter_number, duration_minutes, output_dir)
        if not path.is_file():
            return None
        try:
            return VideoScript.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_chapter_package(self, package: ChapterPackage, output_dir: Path | None = None) -> Path:
        root = output_dir or self.base_dir
        return atomic_write_json(
            root / f"capitulo_{package.chapter_number:02d}.json",
            package.model_dump(),
        )

    def load_video_manifest(
        self,
        chapter_number: int,
        duration_minutes: int,
        output_dir: Path | None = None,
    ) -> VideoManifest | None:
        mpath = self.video_manifest_path(chapter_number, duration_minutes, output_dir)
        if not mpath.is_file():
            return None
        try:
            return VideoManifest.model_validate_json(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_video_manifest(self, manifest: VideoManifest, output_dir: Path | None = None) -> Path:
        mpath = self.video_manifest_path(
            manifest.chapter_number, manifest.script_duration_minutes, output_dir
        )
        return atomic_write_json(mpath, manifest.model_dump())

    def init_or_load_video_manifest(
        self,
        *,
        chapter_number: int,
        script: VideoScript,
        script_path: Path,
        config_hash: str,
        output_dir: Path | None = None,
        resume: bool = True,
    ) -> VideoManifest:
        root = output_dir or self.base_dir
        self.base_dir = root
        current_script_hash = sha256_text(script.narration)
        existing = (
            self.load_video_manifest(chapter_number, script.duration_minutes, root)
            if resume
            else None
        )
        rel_script = self.relative_str(script_path)

        if (
            existing is not None
            and existing.script_hash == current_script_hash
            and existing.config_hash == config_hash
        ):
            return existing

        manifest = VideoManifest(
            job_id=self.job_id,
            chapter_number=chapter_number,
            script_duration_minutes=script.duration_minutes,
            status="script_ready",
            script_path=rel_script,
            attempts=(existing.attempts if existing else 0),
            script_hash=current_script_hash,
            config_hash=config_hash,
            prompt_version=PROMPT_VERSION,
        )
        self.save_video_manifest(manifest, root)
        return manifest

    def update_manifest_status(
        self,
        manifest: VideoManifest,
        status: VideoStatus,
        output_dir: Path | None = None,
        **updates: Any,
    ) -> VideoManifest:
        updated = manifest.model_copy(
            update={
                "status": status,
                "failed_stage": None if status != "failed" else updates.get("failed_stage"),
                "error_message": None if status != "failed" else updates.get("error_message"),
                **updates,
            }
        )
        self.save_video_manifest(updated, output_dir)
        return updated

    def mark_failed(
        self,
        manifest: VideoManifest,
        *,
        stage: str,
        error: Exception,
        output_dir: Path | None = None,
    ) -> VideoManifest:
        resolved_stage = error.stage if isinstance(error, LCBooksError) else stage
        retryable = getattr(error, "retryable", True)
        updated = manifest.model_copy(
            update={
                "status": "failed",
                "failed_stage": resolved_stage,
                "error_message": sanitize_message(str(error)),
                "retryable": retryable,
            }
        )
        self.save_video_manifest(updated, output_dir)
        return updated

    def save_storyboard(
        self,
        scenes: list[StoryboardScene],
        manifest: VideoManifest,
        output_dir: Path | None = None,
    ) -> tuple[Path, VideoManifest]:
        vdir = self.video_dir(manifest.chapter_number, manifest.script_duration_minutes, output_dir)
        sb_path = vdir / "storyboard.json"
        atomic_write_json(sb_path, [s.model_dump() for s in scenes])
        updated = self.update_manifest_status(
            manifest,
            "storyboard_ready",
            output_dir=output_dir,
            storyboard_path=self.relative_str(sb_path),
        )
        return sb_path, updated

    def save_quality_report(
        self,
        report: QualityReport,
        manifest: VideoManifest,
        output_dir: Path | None = None,
    ) -> tuple[Path, VideoManifest]:
        vdir = self.video_dir(manifest.chapter_number, manifest.script_duration_minutes, output_dir)
        q_path = vdir / "quality_report.json"
        atomic_write_json(q_path, report.model_dump())
        updated = self.update_manifest_status(
            manifest,
            "quality_checked",
            output_dir=output_dir,
            quality_report_path=self.relative_str(q_path),
            duration_seconds=report.duration_seconds,
            width=report.width,
            height=report.height,
        )
        return q_path, updated

    def record_failure(
        self,
        boundary: ChapterBoundary,
        duration_minutes: int,
        error: Exception,
        output_dir: Path | None = None,
    ) -> None:
        self._failures.append(
            {
                "chapter_number": boundary.number,
                "duration_minutes": duration_minutes,
                "stage": getattr(error, "stage", "unknown"),
                "error": sanitize_message(str(error)),
            }
        )
        if output_dir is not None:
            self.write_global_manifest(output_dir)

    def write_global_manifest(self, output_dir: Path | None = None) -> Path:
        root = output_dir or self.base_dir
        videos: list[dict[str, Any]] = []
        for mfile in sorted(root.glob("chapter_*/video_*min/manifest.json")):
            try:
                videos.append(json.loads(mfile.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        payload = {
            "job_id": self.job_id,
            "prompt_version": PROMPT_VERSION,
            "videos": videos,
            "failures": self._failures,
        }
        return atomic_write_json(root / "manifest.json", payload)

    def write_consolidated_outputs(
        self,
        output_dir: Path | None = None,
        packages: list[ChapterPackage] | None = None,
    ) -> None:
        root = output_dir or self.base_dir
        if packages is not None:
            atomic_write_json(
                root / "resultado_completo.json",
                [p.model_dump() for p in packages],
            )
            self._write_markdown(packages, root / "roteiros_e_ideias.md")
        self.write_global_manifest(root)

    @staticmethod
    def _write_markdown(packages: list[ChapterPackage], output: Path) -> None:
        lines = [
            "# Roteiros de vídeo por capítulo",
            "",
            "Gerado automaticamente com LangChain + Gemini.",
            "",
        ]
        for p in packages:
            lines += [
                f"## Capítulo {p.chapter_number}: {p.chapter_title}",
                f"**Páginas:** {p.page_range}",
                "",
                "### Resumo",
                p.summary.thesis,
                "",
            ]
            lines.append("**Pontos-chave:** " + "; ".join(p.summary.key_points))
            lines += ["", "### Roteiros"]
            for s in p.scripts:
                lines += [
                    f"#### {s.title} ({s.duration_minutes} min)",
                    f"**Público:** {s.target_audience}",
                    f"**Hook:** {s.hook}",
                    "",
                    s.narration,
                    "",
                    f"**CTA:** {s.cta}",
                    "",
                ]
            lines += ["### Ideias de imagens", *[f"- {idea}" for idea in p.image_ideas], ""]
        atomic_write_text(output, "\n".join(lines), encoding="utf-8")
