from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from lcbooks.exceptions import TTSServiceError
from lcbooks.media.captions import TimedSpeechSegment
from lcbooks.schemas.script import NarrationSegment


class TTSProvider(Protocol):
    def synthesize(self, text: str, output_path: Path) -> Path:
        """Gera um segmento de áudio a partir do texto informado."""
        ...


def _resolve_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    venv_candidate = Path(__file__).resolve().parents[4] / ".venv" / "bin" / name
    if venv_candidate.is_file():
        return str(venv_candidate)
    return None


def _write_sine_wav(path: Path, duration_seconds: float, sample_rate: int = 24000) -> None:
    num_samples = max(int(sample_rate * 0.2), int(sample_rate * duration_seconds))
    amplitude = 8000
    freq = 440.0
    frames = bytearray()
    for i in range(num_samples):
        sample = int(amplitude * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
        frames.extend(struct.pack("<h", sample))

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))


class FakeTTSProvider:
    """Provider TTS determinístico para testes locais sem custo externo."""

    def __init__(self, *, seconds_per_word: float = 0.25) -> None:
        self.seconds_per_word = seconds_per_word

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not text or not text.strip():
            raise ValueError("Não é permitido sintetizar um segmento vazio")

        words = len(text.split())
        duration = max(0.6, round(words * self.seconds_per_word, 2))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        temp_wav = output_path.with_suffix(".tmp.wav")
        _write_sine_wav(temp_wav, duration_seconds=duration)

        ffmpeg_bin = _resolve_binary("ffmpeg")
        if output_path.suffix.lower() == ".mp3" and ffmpeg_bin:
            temp_mp3 = output_path.with_suffix(".tmp.mp3")
            res = subprocess.run(
                [
                    ffmpeg_bin,
                    "-hide_banner",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(temp_wav),
                    "-ar",
                    "24000",
                    "-ac",
                    "1",
                    "-b:a",
                    "128k",
                    str(temp_mp3),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            temp_wav.unlink(missing_ok=True)
            if res.returncode == 0 and temp_mp3.is_file():
                temp_mp3.replace(output_path)
                return output_path
            temp_mp3.unlink(missing_ok=True)
            _write_sine_wav(temp_wav, duration_seconds=duration)

        temp_wav.replace(output_path)
        return output_path


def measure_audio_duration(audio_path: Path) -> float:
    """Mede a duração real do arquivo de áudio via ffprobe ou fallback WAV."""
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise TTSServiceError(f"Arquivo de áudio ausente ou vazio: {audio_path}", retryable=True)

    ffprobe_bin = _resolve_binary("ffprobe")
    if ffprobe_bin:
        res = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if res.returncode == 0:
            payload = json.loads(res.stdout or "{}")
            duration = float(payload.get("format", {}).get("duration") or 0.0)
            if duration > 0:
                return duration

    try:
        with wave.open(str(audio_path), "rb") as wav_in:
            frames = wav_in.getnframes()
            rate = wav_in.getframerate()
            if rate > 0 and frames > 0:
                return frames / float(rate)
    except wave.Error:
        pass

    return max(0.5, audio_path.stat().st_size / 16000.0)


class AudioAssembler:
    """Sintetiza segmentos individuais, mede durações e concatena em narration.mp3."""

    def __init__(self, tts_provider: TTSProvider) -> None:
        self.tts_provider = tts_provider

    def synthesize_script_segments(
        self,
        segments: Sequence[NarrationSegment],
        audio_dir: Path,
        *,
        resume: bool = True,
    ) -> tuple[Path, list[TimedSpeechSegment], float]:
        if not segments:
            raise ValueError("Nenhum segmento narrado fornecido para síntese de áudio")

        audio_dir.mkdir(parents=True, exist_ok=True)
        segment_files: list[Path] = []
        timed_segments: list[TimedSpeechSegment] = []
        cursor = 0.0

        for idx, segment in enumerate(segments, start=1):
            seg_path = audio_dir / f"segment_{idx:03d}.mp3"
            if not (resume and seg_path.is_file() and seg_path.stat().st_size > 0):
                try:
                    self.tts_provider.synthesize(segment.text, seg_path)
                except Exception as exc:
                    if isinstance(exc, TTSServiceError):
                        raise
                    raise TTSServiceError(
                        f"Falha ao sintetizar segmento {segment.id} (#{idx}): {exc}",
                        retryable=True,
                    ) from exc

            seg_duration = measure_audio_duration(seg_path)
            start = cursor
            end = round(cursor + seg_duration, 3)
            timed_segments.append(
                TimedSpeechSegment(
                    text=segment.text,
                    start_seconds=start,
                    end_seconds=end,
                    segment_id=segment.id,
                )
            )
            cursor = end
            segment_files.append(seg_path)

        narration_path = audio_dir / "narration.mp3"
        self._concatenate_audio(segment_files, narration_path)
        total_duration = measure_audio_duration(narration_path)
        return narration_path, timed_segments, total_duration

    @staticmethod
    def _concatenate_audio(segment_files: list[Path], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_out = output_path.with_suffix(".tmp.mp3")
        ffmpeg_bin = _resolve_binary("ffmpeg")

        if ffmpeg_bin:
            inputs: list[str] = []
            filter_parts: list[str] = []
            for idx, seg_file in enumerate(segment_files):
                inputs.extend(["-i", str(seg_file)])
                filter_parts.append(f"[{idx}:a:0]")
            concat_filter = f"{''.join(filter_parts)}concat=n={len(segment_files)}:v=0:a=1[aout]"
            cmd = [
                ffmpeg_bin,
                "-hide_banner",
                "-y",
                "-loglevel",
                "error",
                *inputs,
                "-filter_complex",
                concat_filter,
                "-map",
                "[aout]",
                "-ar",
                "24000",
                "-ac",
                "1",
                "-b:a",
                "192k",
                str(temp_out),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
            if res.returncode != 0:
                temp_out.unlink(missing_ok=True)
                raise TTSServiceError(
                    f"Falha ao concatenar narração com FFmpeg: {(res.stderr or '')[-1000:]}",
                    retryable=True,
                )
            temp_out.replace(output_path)
            return output_path

        # Fallback caso FFmpeg não esteja presente em ambiente de teste unitário puro
        shutil.copyfile(segment_files[0], temp_out)
        temp_out.replace(output_path)
        return output_path
