from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from lcbooks.config import Settings, get_settings
from lcbooks.llm.client import LLMClient
from lcbooks.logging_config import configure_logging, log_event
from lcbooks.media.ffmpeg_renderer import FFmpegRenderer, RenderOptions
from lcbooks.media.quality import VideoQualityChecker
from lcbooks.media.scenes.pillow_provider import PillowSceneProvider
from lcbooks.media.tts.base import FakeTTSProvider, TTSProvider
from lcbooks.media.tts.google_cloud import GoogleCloudTTS
from lcbooks.pdf.chapter_detector import ChapterDetector
from lcbooks.pdf.extractor import PdfExtractor
from lcbooks.schemas.chapters import ChapterPackage
from lcbooks.schemas.script import VideoScript
from lcbooks.services.script_generator import ScriptGenerator
from lcbooks.services.storyboard import StoryboardGenerator
from lcbooks.services.summarizer import ChapterSummarizer
from lcbooks.services.video_pipeline import VideoPipeline
from lcbooks.storage.artifacts import ArtifactStore, atomic_write_json


@dataclass
class RunResult:
    packages: list[ChapterPackage] = field(default_factory=list)
    failed_artifacts: list[tuple[int, int]] = field(default_factory=list)
    video_paths: list[Path] = field(default_factory=list)


def build_tts_provider(settings: Settings) -> TTSProvider:
    if settings.tts_provider == "fake":
        return FakeTTSProvider()
    return GoogleCloudTTS(
        language_code=settings.tts_language,
        voice_name=settings.tts_voice,
        speaking_rate=settings.tts_speaking_rate,
    )


class BookPipeline:
    """Orquestra a extração de PDF, capítulos, resumos, roteiros e vídeos incrementais."""

    def __init__(
        self,
        extractor: PdfExtractor,
        chapter_detector: ChapterDetector,
        summarizer: ChapterSummarizer,
        script_generator: ScriptGenerator,
        video_pipeline: VideoPipeline | None,
        artifact_store: ArtifactStore,
        logger: logging.Logger | None = None,
    ) -> None:
        self.extractor = extractor
        self.chapter_detector = chapter_detector
        self.summarizer = summarizer
        self.script_generator = script_generator
        self.video_pipeline = video_pipeline
        self.store = artifact_store
        self.logger = logger or configure_logging()

    @classmethod
    def from_environment(
        cls,
        *,
        model: str | None = None,
        render_videos: bool = False,
        settings: Settings | None = None,
        verbose: bool = False,
    ) -> BookPipeline:
        cfg = settings or get_settings()
        logger = configure_logging(verbose=verbose)
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        llm = LLMClient(
            model=model or cfg.gemini_model,
            api_key=cfg.gemini_api_key or cfg.google_api_key,
            max_retries=cfg.max_retry_attempts,
        )
        extractor = PdfExtractor(
            max_bytes=cfg.max_pdf_mb * 1024 * 1024,
            max_pages=cfg.max_pdf_pages,
        )
        chapter_detector = ChapterDetector(llm=llm)
        summarizer = ChapterSummarizer(llm=llm, max_chars_per_chunk=cfg.max_chapter_chars_per_chunk)
        script_generator = ScriptGenerator(llm=llm)
        store = ArtifactStore(Path("saida_livro"), job_id=job_id)

        video_pipeline: VideoPipeline | None = None
        if render_videos:
            renderer = FFmpegRenderer(
                options=RenderOptions(
                    width=cfg.video_width,
                    height=cfg.video_height,
                    fps=cfg.video_fps,
                    crf=cfg.video_crf,
                    preset=cfg.video_preset,
                    burn_captions=cfg.burn_captions,
                )
            )
            video_pipeline = VideoPipeline(
                artifact_store=store,
                storyboard_generator=StoryboardGenerator(),
                scene_provider=PillowSceneProvider(
                    width=cfg.video_width,
                    height=cfg.video_height,
                    font_path=cfg.lcbooks_font_path,
                ),
                tts_provider=build_tts_provider(cfg),
                renderer=renderer,
                quality_checker=VideoQualityChecker(
                    expected_width=cfg.video_width,
                    expected_height=cfg.video_height,
                ),
                logger=logger,
            )

        return cls(
            extractor=extractor,
            chapter_detector=chapter_detector,
            summarizer=summarizer,
            script_generator=script_generator,
            video_pipeline=video_pipeline,
            artifact_store=store,
            logger=logger,
        )

    def run(
        self,
        pdf_path: Path,
        output_dir: Path,
        expected_chapters: int = 25,
        durations: list[int] | None = None,
        render_videos: bool = True,
        aspect_ratio: str = "16:9",
        resume: bool = True,
        continue_on_error: bool = True,
        force: bool = False,
    ) -> RunResult:
        started_at = time.monotonic()
        effective_resume = False if force else resume
        requested_durations = durations or [20, 3, 1]
        for dur in requested_durations:
            if dur not in {1, 3, 20}:
                raise ValueError(f"Duração inválida ({dur}). Use apenas 1, 3 ou 20.")

        output_dir.mkdir(parents=True, exist_ok=True)
        self.store.base_dir = output_dir
        log_event(self.logger, "job_started", job_id=self.store.job_id, stage="init")

        pages = self.extractor.extract(pdf_path)
        self.store.save_book_metadata(pdf_path, len(pages), output_dir)
        log_event(
            self.logger,
            "pdf_extracted",
            job_id=self.store.job_id,
            stage="pdf_extraction",
            pages=len(pages),
        )

        chapter_map = self.chapter_detector.detect_map(pages, expected_chapters)
        boundaries = chapter_map.chapters
        self.store.save_chapter_map(chapter_map, output_dir)
        log_event(
            self.logger,
            "chapter_map_validated",
            job_id=self.store.job_id,
            stage="chapter_detection",
            chapters=len(boundaries),
        )

        packages: list[ChapterPackage] = []
        failed_artifacts: list[tuple[int, int]] = []
        video_paths: list[Path] = []

        for boundary in boundaries:
            chapter_text = self.extractor.get_range(pages, boundary.start_page, boundary.end_page)
            summary = (
                self.store.load_summary(boundary.number, output_dir) if effective_resume else None
            )
            if summary is None:
                summary = self.summarizer.summarize(chapter_text, boundary)
                self.store.save_summary(boundary, summary, output_dir)
            log_event(
                self.logger,
                "summary_generated",
                job_id=self.store.job_id,
                chapter_number=boundary.number,
                stage="summary",
            )

            chapter_scripts: list[VideoScript] = []
            for duration in requested_durations:
                try:
                    script = (
                        self.store.load_script(boundary.number, duration, output_dir)
                        if effective_resume
                        else None
                    )
                    if script is None:
                        script = self.script_generator.generate(
                            summary, boundary, duration_minutes=duration
                        )
                    script_path = self.store.save_script(boundary, script, output_dir)
                    chapter_scripts.append(script)
                    log_event(
                        self.logger,
                        "script_generated",
                        job_id=self.store.job_id,
                        chapter_number=boundary.number,
                        script_duration=duration,
                        stage="script_generation",
                    )

                    # O vídeo é processado logo depois deste roteiro, sem esperar o fim do livro.
                    if render_videos and self.video_pipeline is not None:
                        video_file = self.video_pipeline.render(
                            script=script,
                            script_path=script_path,
                            chapter=boundary,
                            output_dir=output_dir,
                            aspect_ratio=aspect_ratio,
                            resume=effective_resume,
                        )
                        video_paths.append(video_file)
                except Exception as exc:
                    self.store.record_failure(boundary, duration, exc, output_dir)
                    failed_artifacts.append((boundary.number, duration))
                    log_event(
                        self.logger,
                        "artifact_failed",
                        job_id=self.store.job_id,
                        chapter_number=boundary.number,
                        script_duration=duration,
                        stage="book_pipeline",
                        level=logging.ERROR,
                        error=str(exc),
                    )
                    if not continue_on_error:
                        raise

            ideas = self.script_generator.generate_image_ideas(summary, boundary)
            pkg = ChapterPackage(
                chapter_number=boundary.number,
                chapter_title=boundary.title,
                page_range=f"{boundary.start_page}-{boundary.end_page}",
                summary=summary,
                scripts=chapter_scripts,
                image_ideas=ideas,
            )
            packages.append(pkg)
            self.store.save_chapter_package(pkg, output_dir)

        self.store.write_consolidated_outputs(output_dir, packages=packages)
        elapsed = round(time.monotonic() - started_at, 2)
        atomic_write_json(
            output_dir / "run_report.json",
            {
                "job_id": self.store.job_id,
                "elapsed_seconds": elapsed,
                "chapters_processed": len(packages),
                "videos_rendered": len(video_paths),
                "failed_artifacts": [
                    {"chapter_number": ch, "duration_minutes": dur} for ch, dur in failed_artifacts
                ],
            },
        )
        return RunResult(
            packages=packages,
            failed_artifacts=failed_artifacts,
            video_paths=video_paths,
        )
