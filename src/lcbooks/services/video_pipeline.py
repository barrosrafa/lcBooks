from __future__ import annotations

import json
import logging
from pathlib import Path

from lcbooks.exceptions import VideoRenderError
from lcbooks.logging_config import configure_logging, log_event
from lcbooks.media.captions import build_srt
from lcbooks.media.ffmpeg_renderer import FFmpegRenderer, RenderOptions, RenderScene
from lcbooks.media.quality import VideoQualityChecker
from lcbooks.media.scenes.base import SceneAssetProvider
from lcbooks.media.scenes.pillow_provider import PillowSceneProvider
from lcbooks.media.tts.base import AudioAssembler, TTSProvider
from lcbooks.schemas.chapters import ChapterBoundary
from lcbooks.schemas.script import VideoScript, ensure_script_segments
from lcbooks.schemas.video import StoryboardScene
from lcbooks.services.storyboard import StoryboardGenerator
from lcbooks.storage.artifacts import ArtifactStore, atomic_write_json, sha256_text


def _aspect_dimensions(
    aspect_ratio: str, default_width: int = 1920, default_height: int = 1080
) -> tuple[int, int]:
    if aspect_ratio == "9:16" and default_width > default_height:
        return default_height, default_width
    return default_width, default_height


class VideoPipeline:
    """Orquestra um roteiro individual desde o storyboard até o MP4 validado."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        storyboard_generator: StoryboardGenerator,
        scene_provider: SceneAssetProvider,
        tts_provider: TTSProvider,
        renderer: FFmpegRenderer,
        quality_checker: VideoQualityChecker,
        logger: logging.Logger | None = None,
    ) -> None:
        self.store = artifact_store
        self.storyboard_generator = storyboard_generator
        self.scene_provider = scene_provider
        self.tts_provider = tts_provider
        self.audio_assembler = AudioAssembler(tts_provider=tts_provider)
        self.renderer = renderer
        self.quality_checker = quality_checker
        self.logger = logger or configure_logging()

    def _config_hash(self, aspect_ratio: str) -> str:
        opts = self.renderer.options
        payload = f"{opts.width}x{opts.height}:{opts.fps}:{opts.burn_captions}:{aspect_ratio}"
        return sha256_text(payload)

    def render(
        self,
        *,
        script: VideoScript,
        script_path: Path,
        chapter: ChapterBoundary,
        output_dir: Path,
        aspect_ratio: str = "16:9",
        resume: bool = True,
    ) -> Path:
        self.store.base_dir = output_dir
        normalized_script = ensure_script_segments(
            script,
            source_pages=list(range(chapter.start_page, chapter.end_page + 1)),
        )
        vdir = self.store.video_dir(chapter.number, normalized_script.duration_minutes, output_dir)
        vdir.mkdir(parents=True, exist_ok=True)
        final_video_path = vdir / "video.mp4"

        config_hash = self._config_hash(aspect_ratio)
        manifest = self.store.init_or_load_video_manifest(
            chapter_number=chapter.number,
            script=normalized_script,
            script_path=script_path,
            config_hash=config_hash,
            output_dir=output_dir,
            resume=resume,
        )

        if (
            resume
            and manifest.status == "completed"
            and final_video_path.is_file()
            and final_video_path.stat().st_size > 1024
        ):
            return final_video_path

        manifest = manifest.model_copy(update={"attempts": manifest.attempts + 1})
        self.store.save_video_manifest(manifest, output_dir)
        current_stage = "storyboard"

        try:
            # 1. Storyboard
            sb_path = vdir / "storyboard.json"
            if resume and sb_path.is_file():
                raw_sb = json.loads(sb_path.read_text(encoding="utf-8"))
                scenes = [StoryboardScene.model_validate(item) for item in raw_sb]
                manifest = self.store.update_manifest_status(
                    manifest,
                    "storyboard_ready",
                    output_dir=output_dir,
                    storyboard_path=self.store.relative_str(sb_path),
                )
            else:
                scenes = self.storyboard_generator.create(normalized_script, chapter)
                sb_path, manifest = self.store.save_storyboard(scenes, manifest, output_dir)

            log_event(
                self.logger,
                "storyboard_generated",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage="storyboard",
                scenes=len(scenes),
            )

            # 2. Cenas visuais (Pillow ou provider externo)
            current_stage = "assets"
            width, height = _aspect_dimensions(
                aspect_ratio,
                self.renderer.options.width,
                self.renderer.options.height,
            )
            scene_provider = self.scene_provider
            if isinstance(scene_provider, PillowSceneProvider) and (
                scene_provider.width != width or scene_provider.height != height
            ):
                scene_provider = PillowSceneProvider(
                    width=width, height=height, font_path=scene_provider.font_path
                )

            updated_scenes: list[StoryboardScene] = []
            for scene in scenes:
                img_path = vdir / "scenes" / f"{scene.scene_id}.png"
                if not (resume and img_path.is_file() and img_path.stat().st_size > 0):
                    scene_provider.create_scene(scene, img_path)
                updated_scenes.append(
                    scene.model_copy(update={"asset_path": self.store.relative_str(img_path)})
                )
            scenes = updated_scenes
            atomic_write_json(sb_path, [s.model_dump() for s in scenes])
            manifest = self.store.update_manifest_status(
                manifest, "assets_ready", output_dir=output_dir
            )
            log_event(
                self.logger,
                "scene_asset_ready",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage="assets",
            )

            # 3. Áudio TTS por segmento + concatenação
            current_stage = "audio"
            audio_dir = vdir / "audio"
            narration_path, timed_segments, total_audio_duration = (
                self.audio_assembler.synthesize_script_segments(
                    normalized_script.segments,
                    audio_dir,
                    resume=resume,
                )
            )
            manifest = self.store.update_manifest_status(
                manifest,
                "audio_ready",
                output_dir=output_dir,
                narration_path=self.store.relative_str(narration_path),
            )
            log_event(
                self.logger,
                "audio_assembled",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage="audio",
                duration_seconds=round(total_audio_duration, 2),
            )

            # Ajusta duração de cada cena à duração real medida dos seus segmentos de áudio
            seg_duration_map: dict[str, float] = {
                ts.segment_id: round(max(0.2, ts.end_seconds - ts.start_seconds), 3)
                for ts in timed_segments
            }
            synced_scenes: list[StoryboardScene] = []
            for scene in scenes:
                measured = sum(seg_duration_map.get(sid, 0.0) for sid in scene.segment_ids)
                final_dur = measured if measured > 0 else scene.duration_seconds
                synced_scenes.append(scene.model_copy(update={"duration_seconds": final_dur}))
            scenes = synced_scenes
            atomic_write_json(sb_path, [s.model_dump() for s in scenes])

            # 4. Legendas SRT
            current_stage = "captions"
            captions_path = vdir / "captions.srt"
            build_srt(timed_segments, captions_path)
            manifest = self.store.update_manifest_status(
                manifest,
                "captions_ready",
                output_dir=output_dir,
                captions_path=self.store.relative_str(captions_path),
            )
            log_event(
                self.logger,
                "captions_written",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage="captions",
            )

            # 5. Renderização FFmpeg
            current_stage = "rendering"
            manifest = self.store.update_manifest_status(
                manifest, "rendering", output_dir=output_dir
            )
            log_event(
                self.logger,
                "video_render_started",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage="rendering",
            )
            render_scenes = [
                RenderScene(
                    image_path=vdir / "scenes" / f"{sc.scene_id}.png",
                    duration_seconds=sc.duration_seconds,
                )
                for sc in scenes
            ]
            renderer = self.renderer
            if renderer.options.width != width or renderer.options.height != height:
                renderer = FFmpegRenderer(
                    options=RenderOptions(
                        width=width,
                        height=height,
                        fps=renderer.options.fps,
                        crf=renderer.options.crf,
                        preset=renderer.options.preset,
                        burn_captions=renderer.options.burn_captions,
                        timeout_seconds=renderer.options.timeout_seconds,
                    )
                )
            try:
                renderer.render(
                    scenes=render_scenes,
                    narration_path=narration_path,
                    output_path=final_video_path,
                    captions_path=captions_path,
                )
            except Exception as exc:
                raise VideoRenderError(str(exc), retryable=True) from exc

            manifest = self.store.update_manifest_status(
                manifest,
                "rendered",
                output_dir=output_dir,
                video_path=self.store.relative_str(final_video_path),
            )
            log_event(
                self.logger,
                "video_render_completed",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage="rendering",
            )

            # 6. Verificação de Qualidade
            current_stage = "quality_check"
            checker = self.quality_checker
            if checker.expected_width != width or checker.expected_height != height:
                checker = VideoQualityChecker(
                    expected_width=width,
                    expected_height=height,
                    min_bytes=checker.min_bytes,
                    duration_tolerance_ratio=checker.duration_tolerance_ratio,
                )
            report = checker.check(
                final_video_path,
                expected_audio_duration=total_audio_duration,
                captions_path=captions_path,
                raise_on_failure=True,
            )
            _, manifest = self.store.save_quality_report(report, manifest, output_dir)
            manifest = self.store.update_manifest_status(
                manifest,
                "completed",
                output_dir=output_dir,
                video_path=self.store.relative_str(final_video_path),
            )
            log_event(
                self.logger,
                "quality_check_passed",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage="quality_check",
                duration_seconds=report.duration_seconds,
            )
            return final_video_path
        except Exception as exc:
            self.store.mark_failed(manifest, stage=current_stage, error=exc, output_dir=output_dir)
            log_event(
                self.logger,
                "artifact_failed",
                job_id=self.store.job_id,
                chapter_number=chapter.number,
                script_duration=normalized_script.duration_minutes,
                stage=current_stage,
                level=logging.ERROR,
                error=str(exc),
            )
            raise
