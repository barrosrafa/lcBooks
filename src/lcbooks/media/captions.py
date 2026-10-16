from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TimedSpeechSegment:
    text: str
    start_seconds: float
    end_seconds: float
    segment_id: str = ""


def _srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _parse_srt_time(ts: str) -> float:
    match = re.match(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$", ts.strip())
    if not match:
        raise ValueError(f"Timestamp SRT inválido: {ts}")
    h, m, s, ms = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s + ms / 1000.0


def _caption_chunks(text: str, max_words: int = 8, max_chars: int = 42) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and (len(current) >= max_words or len(candidate) > max_chars):
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def build_srt(segments: list[TimedSpeechSegment], output_path: Path) -> Path:
    cues: list[tuple[float, float, str]] = []
    last_end = 0.0
    for segment in segments:
        text = " ".join(segment.text.replace("\x00", " ").split())
        start = max(last_end, segment.start_seconds, 0.0)
        end = segment.end_seconds
        if not text or end <= start:
            continue

        chunks = _caption_chunks(text)
        weights = [max(1, len(chunk.split())) for chunk in chunks]
        total_weight = sum(weights)
        cursor = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            next_cursor = (
                end if index == len(chunks) - 1 else cursor + (end - start) * weight / total_weight
            )
            if next_cursor > cursor:
                cues.append((cursor, next_cursor, chunk))
            cursor = next_cursor
        last_end = end

    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for number, (start, end, text) in enumerate(cues, start=1):
        lines.extend([str(number), f"{_srt_time(start)} --> {_srt_time(end)}", text, ""])
    temporary = output_path.with_suffix(".tmp.srt")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(output_path)
    return output_path


def parse_srt_cues(srt_path: Path) -> list[tuple[int, float, float, str]]:
    """Lê um arquivo SRT e retorna (numero, inicio_seg, fim_seg, texto)."""
    content = srt_path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    blocks = re.split(r"\n\s*\n", content)
    parsed: list[tuple[int, float, float, str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        number = int(lines[0])
        start_str, end_str = [part.strip() for part in lines[1].split("-->")]
        text = "\n".join(lines[2:])
        parsed.append((number, _parse_srt_time(start_str), _parse_srt_time(end_str), text))
    return parsed
