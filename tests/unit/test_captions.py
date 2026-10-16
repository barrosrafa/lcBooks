from __future__ import annotations

from pathlib import Path

import pytest

from lcbooks.media.captions import (
    TimedSpeechSegment,
    _srt_time,
    build_srt,
    parse_srt_cues,
)
from lcbooks.media.tts.base import AudioAssembler, FakeTTSProvider
from lcbooks.media.tts.google_cloud import GoogleCloudTTS
from lcbooks.schemas.script import NarrationSegment


def test_srt_time_formatting_including_over_one_hour() -> None:
    assert _srt_time(0.0) == "00:00:00,000"
    assert _srt_time(65.432) == "00:01:05,432"
    assert _srt_time(3661.5) == "01:01:01,500"


def test_build_srt_handles_overlapping_accents_long_sentences_and_empty_text(
    tmp_path: Path,
) -> None:
    segments = [
        TimedSpeechSegment(
            text="   ",
            start_seconds=0.0,
            end_seconds=1.0,
        ),
        TimedSpeechSegment(
            text="Atenção: coração, razão e emoção! Uma frase longa com muitas palavras para testar a quebra automática em blocos menores e legíveis na tela.",
            start_seconds=0.5,
            end_seconds=6.5,
        ),
        # Intervalo sobreposto com o anterior (início 5.0 < 6.5) deve ser corrigido monotonicamente
        TimedSpeechSegment(
            text="Segundo trecho após ajuste monotônico\x00 com caractere nulo removido.",
            start_seconds=5.0,
            end_seconds=9.0,
        ),
        # Timestamp acima de 1 hora
        TimedSpeechSegment(
            text="Bloco após uma hora de vídeo.",
            start_seconds=3605.0,
            end_seconds=3608.25,
        ),
    ]
    out_srt = tmp_path / "captions.srt"
    result_path = build_srt(segments, out_srt)

    assert result_path == out_srt
    content = out_srt.read_text(encoding="utf-8")
    assert "\x00" not in content
    assert "Atenção: coração, razão e emoção!" in content
    assert "01:00:05,000 --> 01:00:08,250" in content

    cues = parse_srt_cues(out_srt)
    assert len(cues) >= 4
    prev_end = 0.0
    for idx, (cue_num, start, end, text) in enumerate(cues, start=1):
        assert cue_num == idx
        assert start >= prev_end - 1e-3
        assert end > start
        assert len(text) <= 60
        prev_end = end


def test_fake_tts_and_google_cloud_empty_text_rejection(tmp_path: Path) -> None:
    fake_tts = FakeTTSProvider()
    with pytest.raises(ValueError, match="segmento vazio"):
        fake_tts.synthesize("   ", tmp_path / "empty.mp3")

    # GoogleCloudTTS sem cliente real rejeita segmento vazio antes de chamar rede
    gcloud_tts = GoogleCloudTTS.__new__(GoogleCloudTTS)
    gcloud_tts.language_code = "pt-BR"
    gcloud_tts.voice_name = "pt-BR-Standard-A"
    gcloud_tts.speaking_rate = 1.0
    gcloud_tts._client = None
    with pytest.raises(ValueError, match="segmento vazio"):
        gcloud_tts.synthesize("", tmp_path / "empty2.mp3")


def test_audio_assembler_synthesizes_and_concatenates_segments(tmp_path: Path) -> None:
    fake_tts = FakeTTSProvider(seconds_per_word=0.15)
    assembler = AudioAssembler(tts_provider=fake_tts)
    segments = [
        NarrationSegment(id="seg_0001", text="Primeira fala de teste curto."),
        NarrationSegment(id="seg_0002", text="Segunda fala de teste complementar."),
    ]
    narration_path, timed_segments, total_duration = assembler.synthesize_script_segments(
        segments, tmp_path / "audio"
    )
    assert narration_path.is_file()
    assert narration_path.stat().st_size > 100
    assert len(timed_segments) == 2
    assert timed_segments[0].start_seconds == 0.0
    assert timed_segments[0].end_seconds > 0.0
    assert timed_segments[1].start_seconds == pytest.approx(timed_segments[0].end_seconds, abs=0.05)
    assert total_duration == pytest.approx(timed_segments[-1].end_seconds, abs=0.2)
