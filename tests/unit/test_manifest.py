from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lcbooks.exceptions import TTSServiceError, VideoQualityError
from lcbooks.media.ffmpeg_renderer import FFmpegRenderer, RenderOptions, RenderScene
from lcbooks.media.quality import VideoQualityChecker
from lcbooks.media.scenes.pillow_provider import PillowSceneProvider
from lcbooks.media.tts.base import FakeTTSProvider
from lcbooks.schemas.chapters import ChapterBoundary
from lcbooks.schemas.script import VideoScript, ensure_script_segments
from lcbooks.schemas.video import StoryboardScene
from lcbooks.services.storyboard import StoryboardGenerator
from lcbooks.services.video_pipeline import VideoPipeline
from lcbooks.storage.artifacts import ArtifactStore


def test_missing_ffmpeg_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="FFmpeg e ffprobe precisam estar instalados"):
        FFmpegRenderer(allow_venv_fallback=False)


def test_partial_mp4_cleaned_up_and_never_published_on_failure(tmp_path: Path) -> None:
    renderer = FFmpegRenderer(
        options=RenderOptions(width=320, height=180, fps=15, burn_captions=False)
    )
    scene_provider = PillowSceneProvider(width=320, height=180)
    img_path = scene_provider.create_scene(
        StoryboardScene(
            scene_id="scene_001",
            segment_ids=["seg_0001"],
            visual_type="title",
            on_screen_text="Cena",
        ),
        tmp_path / "scene_001.png",
    )
    corrupt_audio = tmp_path / "corrupt.mp3"
    corrupt_audio.write_bytes(b"not-an-audio-stream")
    output_mp4 = tmp_path / "video.mp4"

    with pytest.raises(RuntimeError):
        renderer.render(
            scenes=[RenderScene(image_path=img_path, duration_seconds=1.0)],
            narration_path=corrupt_audio,
            output_path=output_mp4,
            captions_path=None,
        )

    assert not output_mp4.exists()
    assert not (tmp_path / "video.partial.mp4").exists()


def test_quality_checker_fails_when_mp4_has_no_audio(tmp_path: Path) -> None:
    renderer = FFmpegRenderer(
        options=RenderOptions(width=320, height=180, fps=15, burn_captions=False)
    )
    scene_provider = PillowSceneProvider(width=320, height=180)
    img_path = scene_provider.create_scene(
        StoryboardScene(
            scene_id="scene_001",
            segment_ids=["seg_0001"],
            visual_type="title",
            on_screen_text="Sem Áudio",
        ),
        tmp_path / "scene.png",
    )
    silent_video = tmp_path / "no_audio.mp4"
    subprocess.run(
        [
            renderer.ffmpeg,
            "-hide_banner",
            "-y",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            "15",
            "-t",
            "1.0",
            "-i",
            str(img_path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(silent_video),
        ],
        check=True,
    )

    checker = VideoQualityChecker(expected_width=320, expected_height=180)
    report = checker.check(silent_video, raise_on_failure=False)
    assert report.passed is False
    assert report.has_audio is False

    with pytest.raises(VideoQualityError, match="áudio"):
        checker.check(silent_video, raise_on_failure=True)


def test_tts_failure_on_segment_2_preserves_segment_1_and_resumes_cleanly(tmp_path: Path) -> None:
    base_tts = FakeTTSProvider(seconds_per_word=0.15)
    calls_by_text: dict[str, int] = {}

    class FlakyTTSProvider:
        def __init__(self, fail_second: bool = True) -> None:
            self.fail_second = fail_second

        def synthesize(self, text: str, output_path: Path) -> Path:
            calls_by_text[text] = calls_by_text.get(text, 0) + 1
            if self.fail_second and "Segundo segmento" in text:
                raise TTSServiceError("Falha simulada de rede no segmento 2", retryable=True)
            return base_tts.synthesize(text, output_path)

    script = ensure_script_segments(
        VideoScript(
            title="Roteiro Retomável",
            duration_minutes=1,
            target_audience="Leitores",
            hook="Gancho",
            beats=["Parte 1", "Parte 2"],
            narration="Primeiro segmento narrado com sucesso.\n\nSegundo segmento que falha na primeira tentativa.",
            cta="Inscreva-se",
            b_roll_and_visuals=["Cena 1", "Cena 2"],
        ),
        source_pages=[1, 2],
    )
    boundary = ChapterBoundary(number=1, title="Cap 1", start_page=1, end_page=2, evidence="")
    store = ArtifactStore(tmp_path)
    script_path = store.save_script(boundary, script)

    flaky_pipeline = VideoPipeline(
        artifact_store=store,
        storyboard_generator=StoryboardGenerator(),
        scene_provider=PillowSceneProvider(width=320, height=180),
        tts_provider=FlakyTTSProvider(fail_second=True),
        renderer=FFmpegRenderer(
            options=RenderOptions(
                width=320, height=180, fps=15, preset="ultrafast", burn_captions=True
            )
        ),
        quality_checker=VideoQualityChecker(expected_width=320, expected_height=180),
    )

    with pytest.raises(TTSServiceError):
        flaky_pipeline.render(
            script=script,
            script_path=script_path,
            chapter=boundary,
            output_dir=tmp_path,
            resume=True,
        )

    video_dir = store.video_dir(boundary.number, script.duration_minutes)
    manifest = store.load_video_manifest(boundary.number, script.duration_minutes)
    assert manifest is not None
    assert manifest.status == "failed"
    assert manifest.failed_stage == "audio"
    assert (video_dir / "audio" / "segment_001.mp3").is_file()
    assert not (video_dir / "video.mp4").exists()

    # Segunda execução com resume=True: segmento 1 não deve ser ressintetizado
    healthy_pipeline = VideoPipeline(
        artifact_store=store,
        storyboard_generator=StoryboardGenerator(),
        scene_provider=PillowSceneProvider(width=320, height=180),
        tts_provider=FlakyTTSProvider(fail_second=False),
        renderer=FFmpegRenderer(
            options=RenderOptions(
                width=320, height=180, fps=15, preset="ultrafast", burn_captions=True
            )
        ),
        quality_checker=VideoQualityChecker(expected_width=320, height_override=180)
        if False
        else VideoQualityChecker(expected_width=320, expected_height=180),
    )
    final_video = healthy_pipeline.render(
        script=script,
        script_path=script_path,
        chapter=boundary,
        output_dir=tmp_path,
        resume=True,
    )
    assert final_video.is_file()
    assert calls_by_text["Primeiro segmento narrado com sucesso."] == 1
    assert calls_by_text["Segundo segmento que falha na primeira tentativa."] == 2

    completed_manifest = store.load_video_manifest(boundary.number, script.duration_minutes)
    assert completed_manifest is not None
    assert completed_manifest.status == "completed"
    assert completed_manifest.duration_seconds and completed_manifest.duration_seconds > 0

    # Terceira execução com resume=True: tudo concluído, nenhuma nova chamada TTS
    reused_video = healthy_pipeline.render(
        script=script,
        script_path=script_path,
        chapter=boundary,
        output_dir=tmp_path,
        resume=True,
    )
    assert reused_video == final_video
    assert calls_by_text["Primeiro segmento narrado com sucesso."] == 1
    assert calls_by_text["Segundo segmento que falha na primeira tentativa."] == 2
