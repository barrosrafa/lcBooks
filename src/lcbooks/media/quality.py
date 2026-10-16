from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lcbooks.exceptions import VideoQualityError
from lcbooks.media.captions import parse_srt_cues
from lcbooks.media.ffmpeg_renderer import require_binary
from lcbooks.schemas.video import QualityReport


def _parse_fps(avg_frame_rate: str) -> float:
    if not avg_frame_rate or avg_frame_rate == "0/0":
        return 0.0
    if "/" in avg_frame_rate:
        num_s, den_s = avg_frame_rate.split("/", 1)
        num, den = float(num_s), float(den_s)
        return round(num / den, 2) if den > 0 else 0.0
    try:
        return round(float(avg_frame_rate), 2)
    except ValueError:
        return 0.0


class VideoQualityChecker:
    """Inspeciona o MP4 final via ffprobe e valida áudio, resolução, duração e SRT."""

    def __init__(
        self,
        *,
        expected_width: int = 1920,
        expected_height: int = 1080,
        min_bytes: int = 1024,
        duration_tolerance_ratio: float = 0.35,
    ) -> None:
        self.expected_width = expected_width
        self.expected_height = expected_height
        self.min_bytes = min_bytes
        self.duration_tolerance_ratio = duration_tolerance_ratio

    def check(
        self,
        video_path: Path,
        *,
        expected_audio_duration: float | None = None,
        captions_path: Path | None = None,
        raise_on_failure: bool = True,
    ) -> QualityReport:
        errors: list[str] = []
        warnings: list[str] = []

        if not video_path.is_file():
            errors.append(f"Arquivo MP4 não encontrado: {video_path}")
            return self._finalize(
                QualityReport(passed=False, errors=errors, warnings=warnings),
                raise_on_failure=raise_on_failure,
            )

        if video_path.stat().st_size < self.min_bytes:
            errors.append(f"Arquivo MP4 menor que o mínimo esperado ({self.min_bytes} bytes)")

        ffprobe = require_binary("ffprobe")
        res = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if res.returncode != 0:
            errors.append(f"ffprobe falhou ao inspecionar MP4: {(res.stderr or '')[-500:]}")
            return self._finalize(
                QualityReport(passed=False, errors=errors, warnings=warnings),
                raise_on_failure=raise_on_failure,
            )

        payload = json.loads(res.stdout or "{}")
        streams = payload.get("streams", [])
        duration = float(payload.get("format", {}).get("duration") or 0.0)

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        video_codec = str(video_stream.get("codec_name") or "") if video_stream else ""
        audio_codec = str(audio_stream.get("codec_name") or "") if audio_stream else ""
        width = int(video_stream.get("width") or 0) if video_stream else 0
        height = int(video_stream.get("height") or 0) if video_stream else 0
        fps = _parse_fps(str(video_stream.get("avg_frame_rate") or "")) if video_stream else 0.0
        has_audio = audio_stream is not None

        if video_stream is None:
            errors.append("Stream de vídeo ausente no MP4")
        if not has_audio:
            errors.append("Stream de áudio ausente no MP4")
        if duration <= 0:
            errors.append("Duração do MP4 deve ser maior que zero")
        if width != self.expected_width or height != self.expected_height:
            errors.append(
                f"Resolução incorreta: obtido {width}x{height}, esperado {self.expected_width}x{self.expected_height}"
            )

        if expected_audio_duration and expected_audio_duration > 0 and duration > 0:
            diff_ratio = abs(duration - expected_audio_duration) / expected_audio_duration
            if (
                diff_ratio > self.duration_tolerance_ratio
                and abs(duration - expected_audio_duration) > 1.5
            ):
                warnings.append(
                    f"Diferença entre duração do vídeo ({duration:.2f}s) e áudio ({expected_audio_duration:.2f}s)"
                )

        captions_present = False
        if captions_path is not None:
            if not captions_path.is_file() or captions_path.stat().st_size == 0:
                errors.append(f"Arquivo de legendas ausente ou vazio: {captions_path}")
            else:
                captions_present = True
                cues = parse_srt_cues(captions_path)
                if cues:
                    max_end = max(c[2] for c in cues)
                    ref_dur = max(duration, expected_audio_duration or 0.0)
                    if ref_dur > 0 and max_end > ref_dur + 1.0:
                        errors.append(
                            f"Último timestamp SRT ({max_end:.2f}s) ultrapassa a duração do áudio/vídeo ({ref_dur:.2f}s)"
                        )

        report = QualityReport(
            passed=len(errors) == 0,
            video_codec=video_codec,
            audio_codec=audio_codec,
            width=width,
            height=height,
            fps=fps,
            duration_seconds=round(duration, 3),
            has_audio=has_audio,
            captions_present=captions_present,
            warnings=warnings,
            errors=errors,
        )
        return self._finalize(report, raise_on_failure=raise_on_failure)

    @staticmethod
    def _finalize(report: QualityReport, *, raise_on_failure: bool) -> QualityReport:
        if not report.passed and raise_on_failure:
            raise VideoQualityError(
                "Falha na verificação de qualidade do MP4: " + "; ".join(report.errors),
                retryable=False,
            )
        return report
