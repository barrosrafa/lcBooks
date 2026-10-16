from __future__ import annotations

import pytest
from pydantic import ValidationError

from lcbooks.config import Settings
from lcbooks.exceptions import LCBooksError, PdfExtractionError, VideoRenderError
from lcbooks.logging_config import sanitize_message
from lcbooks.schemas.chapters import (
    ChapterBoundary,
    ChapterSummary,
    SupportedClaim,
)
from lcbooks.schemas.script import VideoScript, ensure_script_segments
from lcbooks.schemas.video import StoryboardScene


def test_video_script_rejects_zero_or_unsupported_duration() -> None:
    with pytest.raises(ValidationError):
        VideoScript(
            title="Teste",
            duration_minutes=0,
            target_audience="Geral",
            hook="Hook",
            beats=["Beat 1"],
            narration="Texto narrado válido.",
            cta="CTA",
        )

    with pytest.raises(ValidationError):
        VideoScript(
            title="Teste",
            duration_minutes=5,
            target_audience="Geral",
            hook="Hook",
            beats=["Beat 1"],
            narration="Texto narrado válido.",
            cta="CTA",
        )


def test_video_script_rejects_empty_narration_or_empty_beats() -> None:
    with pytest.raises(ValidationError):
        VideoScript(
            title="Teste",
            duration_minutes=3,
            target_audience="Geral",
            hook="Hook",
            beats=["Beat 1"],
            narration="",
            cta="CTA",
        )

    with pytest.raises(ValidationError):
        VideoScript(
            title="Teste",
            duration_minutes=3,
            target_audience="Geral",
            hook="Hook",
            beats=[],
            narration="Narração válida.",
            cta="CTA",
        )


def test_storyboard_scene_rejects_missing_segments_or_invalid_duration() -> None:
    with pytest.raises(ValidationError):
        StoryboardScene(
            scene_id="scene_001",
            segment_ids=[],
            visual_type="title",
        )

    with pytest.raises(ValidationError):
        StoryboardScene(
            scene_id="scene_001",
            segment_ids=["seg_0001"],
            visual_type="title",
            duration_seconds=0,
        )


def test_ensure_script_segments_creates_segments_from_legacy_narration() -> None:
    script = VideoScript(
        title="Roteiro Antigo",
        duration_minutes=1,
        target_audience="Leitores",
        hook="Gancho inicial.",
        beats=["Introdução", "Conclusão"],
        narration="Primeira frase importante do capítulo.\n\nSegunda frase com conclusão forte.",
        cta="Inscreva-se.",
        b_roll_and_visuals=["Livro aberto sobre a mesa"],
    )
    assert script.segments == []

    normalized = ensure_script_segments(script, source_pages=[10, 11])
    assert len(normalized.segments) == 2
    assert normalized.segments[0].id == "seg_0001"
    assert "Primeira frase" in normalized.segments[0].text
    assert normalized.segments[0].source_pages == [10, 11]


def test_chapter_summary_supports_legacy_strings_and_supported_claims() -> None:
    legacy_summary = ChapterSummary(
        title="Capítulo 1",
        thesis="Tese central",
        key_points=["Ponto 1", "Ponto 2"],
        examples=["Exemplo 1"],
        quotes_or_definitions=["Definição 1"],
        audience_takeaway="Takeaway",
        visual_motifs=["Motivo 1"],
    )
    assert legacy_summary.key_points == ["Ponto 1", "Ponto 2"]

    claim_summary = ChapterSummary(
        title="Capítulo 1",
        thesis="Tese central",
        key_points=[
            SupportedClaim(claim="Ponto rastreável", source_pages=[1, 2], confidence="high")
        ],
        examples=[SupportedClaim(claim="Exemplo rastreável", source_pages=[2])],
        definitions=[SupportedClaim(claim="Conceito X", source_pages=[3])],
        audience_takeaway="Takeaway",
        visual_motifs=["Motivo 1"],
        limitations=["Amostra parcial"],
    )
    assert claim_summary.key_points == ["Ponto rastreável"]
    assert len(claim_summary.supported_claims) >= 1
    assert claim_summary.supported_claims[0].source_pages == [1, 2]
    assert claim_summary.definitions[0].claim == "Conceito X"


def test_settings_hides_api_keys_in_repr_and_safe_dict() -> None:
    settings = Settings(gemini_api_key="super-secret-key-123", google_api_key="other-secret-456")
    assert "super-secret-key-123" not in repr(settings)
    assert "other-secret-456" not in repr(settings)
    safe = settings.safe_dict()
    assert "gemini_api_key" not in safe
    assert "google_api_key" not in safe
    assert safe["tts_provider"] == "google_cloud"


def test_sanitize_message_redacts_secrets_and_truncates() -> None:
    raw = "Erro ao chamar API com key=AIzaSySecretKey1234567890 e token super-secret"
    clean = sanitize_message(raw, secrets=["super-secret"])
    assert "super-secret" not in clean
    assert "AIzaSySecretKey1234567890" not in clean


def test_domain_exceptions_include_stage_and_retryable() -> None:
    err = PdfExtractionError("PDF inválido", retryable=False)
    assert isinstance(err, LCBooksError)
    assert err.stage == "pdf_extraction"
    assert err.retryable is False

    render_err = VideoRenderError("Falha no FFmpeg", retryable=True)
    assert render_err.stage == "rendering"
    assert render_err.retryable is True


def test_llm_client_retries_transient_error_but_not_validation() -> None:
    from lcbooks.llm.client import LLMClient
    from lcbooks.llm.prompts import PROMPT_VERSION

    assert PROMPT_VERSION.startswith("v")

    attempts = {"count": 0}

    class TransientRunner:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise TimeoutError("Transient timeout")
            return ChapterSummary(title="OK", thesis="Tese")

    client = LLMClient(raw_llm=TransientRunner(), max_retries=2, retry_delay_seconds=0.0)
    res = client.generate_structured(system_prompt="sys", user_prompt="usr", schema=ChapterSummary)
    assert res is not None
    assert res.title == "OK"
    assert attempts["count"] == 2


def test_script_generator_validates_word_count_and_populates_segments() -> None:
    from lcbooks.services.script_generator import ScriptGenerator

    class FakeScriptLLM:
        def __init__(self) -> None:
            self.calls = 0

        def generate_structured(self, *, system_prompt: str, user_prompt: str, schema):
            self.calls += 1
            if self.calls == 1:
                # Muito curto para 1 minuto (apenas 5 palavras -> dispara 1 regeneração direcionada)
                return VideoScript(
                    title="Curto demais",
                    duration_minutes=1,
                    target_audience="Leitores",
                    hook="Hook",
                    beats=["Beat 1"],
                    narration="Texto curto demais aqui hoje.",
                    cta="CTA",
                )
            words = " ".join(["palavra"] * 135)
            return VideoScript(
                title="Roteiro de 1 Minuto Ideal",
                duration_minutes=1,
                target_audience="Leitores",
                hook="Gancho forte",
                beats=["Introdução", "Clímax"],
                narration=f"Primeiro parágrafo {words[:300]}.\n\nSegundo parágrafo {words[300:]}.",
                cta="Inscreva-se no canal!",
                b_roll_and_visuals=["Cena 1", "Cena 2"],
            )

    llm = FakeScriptLLM()
    gen = ScriptGenerator(llm=llm, words_per_minute=140)
    boundary = ChapterBoundary(number=1, title="Cap 1", start_page=5, end_page=8, evidence="Cap 1")
    summary = ChapterSummary(
        title="Cap 1",
        thesis="Tese central do capítulo.",
        key_points=["Ponto A", "Ponto B"],
        source_pages=[5, 6],
    )
    script = gen.generate(summary, boundary, duration_minutes=1)
    assert llm.calls == 2
    assert script.duration_minutes == 1
    assert len(script.segments) >= 2
    assert script.segments[0].id == "seg_0001"
    assert script.segments[0].source_pages == [5, 6]


def test_storyboard_and_pillow_scene_provider(tmp_path) -> None:
    from PIL import Image

    from lcbooks.media.scenes.pillow_provider import PillowSceneProvider
    from lcbooks.services.storyboard import StoryboardGenerator

    script = ensure_script_segments(
        VideoScript(
            title="Capítulo 1: O Mistério",
            duration_minutes=1,
            target_audience="Geral",
            hook="Gancho",
            beats=["Abertura", "Desenvolvimento", "Conclusão"],
            narration="Primeira parte da narração.\n\nSegunda parte da narração.\n\nTerceira parte final.",
            cta="Inscreva-se",
            b_roll_and_visuals=["Visual A", "Visual B", "Visual C"],
        ),
        source_pages=[1, 2],
    )
    boundary = ChapterBoundary(number=1, title="O Mistério", start_page=1, end_page=2, evidence="")
    sb_gen = StoryboardGenerator()
    scenes = sb_gen.create(script, boundary)

    assert len(scenes) == len(script.segments)
    covered_segments = [seg_id for sc in scenes for seg_id in sc.segment_ids]
    assert covered_segments == [s.id for s in script.segments]

    provider = PillowSceneProvider(width=640, height=360)
    out_img = tmp_path / "scenes" / "scene_001.png"
    created = provider.create_scene(scenes[0], out_img)
    assert created.is_file()
    with Image.open(created) as img:
        assert img.size == (640, 360)
