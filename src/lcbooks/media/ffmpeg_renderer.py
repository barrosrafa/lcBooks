from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lcbooks.logging_config import sanitize_message

_ORIG_WHICH = shutil.which


@dataclass(frozen=True)
class RenderScene:
    image_path: Path
    duration_seconds: float


@dataclass(frozen=True)
class RenderOptions:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    burn_captions: bool = True
    timeout_seconds: int = 7200


def require_binary(name: str, *, allow_venv_fallback: bool = True) -> str:
    binary = shutil.which(name)
    if binary:
        return binary
    if allow_venv_fallback and shutil.which is _ORIG_WHICH:
        candidates = [
            Path(sys.executable).parent / name,
            Path(__file__).resolve().parents[3] / ".venv" / "bin" / name,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError(
        f"FFmpeg e ffprobe precisam estar instalados e disponíveis no PATH (ausente: {name})."
    )


def _escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return re.sub(r"([\\':,;\[\]])", r"\\\1", value)


def _run(command: list[str], timeout_seconds: int) -> None:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        tail = sanitize_message((result.stderr or "")[-3000:])
        raise RuntimeError(f"FFmpeg falhou ({result.returncode}): {tail}")


class FFmpegRenderer:
    def __init__(
        self,
        options: RenderOptions | None = None,
        *,
        allow_venv_fallback: bool = True,
    ) -> None:
        self.options = options or RenderOptions()
        self.ffmpeg = require_binary("ffmpeg", allow_venv_fallback=allow_venv_fallback)
        self.ffprobe = require_binary("ffprobe", allow_venv_fallback=allow_venv_fallback)

    def render(
        self,
        scenes: list[RenderScene],
        narration_path: Path,
        output_path: Path,
        captions_path: Path | None = None,
    ) -> Path:
        if not scenes:
            raise ValueError("Não é possível renderizar um vídeo sem cenas")
        if not narration_path.is_file():
            raise FileNotFoundError(f"Narração não encontrada: {narration_path}")
        for scene in scenes:
            if not scene.image_path.is_file() or scene.duration_seconds <= 0:
                raise ValueError(f"Cena inválida: {scene.image_path}")
        if self.options.burn_captions and (captions_path is None or not captions_path.is_file()):
            raise ValueError("Legenda embutida habilitada, mas o arquivo SRT não existe")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = output_path.with_name(output_path.stem + ".partial.mp4")

        with tempfile.TemporaryDirectory(prefix="lcbooks_srt_") as tmp_dir:
            effective_srt: Path | None = None
            if self.options.burn_captions and captions_path is not None:
                effective_srt = Path(tmp_dir) / "captions.srt"
                shutil.copyfile(captions_path, effective_srt)

            command = [self.ffmpeg, "-hide_banner", "-y", "-loglevel", "error"]
            for scene in scenes:
                command += [
                    "-loop",
                    "1",
                    "-framerate",
                    str(self.options.fps),
                    "-t",
                    f"{scene.duration_seconds:.3f}",
                    "-i",
                    str(scene.image_path),
                ]
            audio_index = len(scenes)
            command += ["-i", str(narration_path)]

            filters: list[str] = []
            for index, scene in enumerate(scenes):
                duration = scene.duration_seconds
                filters.append(
                    f"[{index}:v]"
                    f"scale={self.options.width}:{self.options.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.options.width}:{self.options.height},setsar=1,fps={self.options.fps},"
                    f"trim=duration={duration:.3f},setpts=PTS-STARTPTS,format=yuv420p[v{index}]"
                )
            joined_inputs = "".join(f"[v{i}]" for i in range(len(scenes)))
            filters.append(f"{joined_inputs}concat=n={len(scenes)}:v=1:a=0[vconcat]")
            video_label = "vconcat"
            if self.options.burn_captions and effective_srt is not None:
                srt_escaped = _escape_filter_path(effective_srt)
                filters.append(f"[vconcat]subtitles=filename='{srt_escaped}'[vout]")
                video_label = "vout"

            command += [
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[{video_label}]",
                "-map",
                f"{audio_index}:a:0",
                "-c:v",
                "libx264",
                "-preset",
                self.options.preset,
                "-crf",
                str(self.options.crf),
                "-r",
                str(self.options.fps),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(partial_path),
            ]
            try:
                _run(command, self.options.timeout_seconds)
                if not partial_path.is_file() or partial_path.stat().st_size < 1024:
                    raise RuntimeError("FFmpeg terminou sem gerar um MP4 válido")

                probe = subprocess.run(
                    [
                        self.ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height",
                        "-of",
                        "json",
                        str(partial_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if probe.returncode != 0:
                    raise RuntimeError(
                        f"ffprobe rejeitou o MP4: {sanitize_message((probe.stderr or '')[-2000:])}"
                    )
                metadata = json.loads(probe.stdout or "{}")
                stream_types = {stream.get("codec_type") for stream in metadata.get("streams", [])}
                duration = float(metadata.get("format", {}).get("duration") or 0)
                if not {"video", "audio"}.issubset(stream_types) or duration <= 0:
                    raise RuntimeError(
                        "MP4 não contém streams de vídeo/áudio válidos ou duração positiva"
                    )

                partial_path.replace(output_path)
                return output_path
            except Exception:
                partial_path.unlink(missing_ok=True)
                raise
