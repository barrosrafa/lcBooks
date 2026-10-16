from __future__ import annotations

import pytest

from lcbooks.exceptions import ChapterDetectionError
from lcbooks.pdf.chapter_detector import (
    likely_chapter_pages,
    normalize_boundaries,
    page_samples,
    validate_boundaries,
)
from lcbooks.pdf.extractor import ExtractedPage
from lcbooks.schemas.chapters import ChapterBoundary, ChapterMap, ChapterSummary, SupportedClaim
from lcbooks.services.summarizer import ChapterSummarizer


class FakeSummaryLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_structured(self, *, system_prompt: str, user_prompt: str, schema):
        self.calls.append(user_prompt)
        return ChapterSummary(
            title="Capítulo Teste",
            thesis="Tese consolidada a partir dos blocos.",
            key_points=[
                SupportedClaim(
                    claim=f"Ponto extraído na chamada {len(self.calls)}", source_pages=[1, 2, 99]
                )
            ],
            examples=[SupportedClaim(claim="Exemplo prático", source_pages=[2])],
            definitions=[SupportedClaim(claim="Definição chave", source_pages=[1])],
            audience_takeaway="Lição principal do capítulo.",
            visual_motifs=["Balança antiga", "Relógio de bolso"],
        )


def test_likely_chapter_pages_and_samples() -> None:
    pages = [
        "Prefácio da obra",
        "Capítulo 1\nO começo da jornada",
        "Texto contínuo da página três",
        "Capítulo 2\nO segundo ato",
    ]
    candidates = likely_chapter_pages(pages)
    assert candidates == [2, 4]
    sample = page_samples(pages, candidates)
    assert "--- PÁGINA 2 ---" in sample
    assert "--- PÁGINA 4 ---" in sample


def test_overlapping_chapters_raise_error_on_strict_validation() -> None:
    mapping = ChapterMap(
        chapters=[
            ChapterBoundary(number=1, title="Cap 1", start_page=1, end_page=5, evidence="Cap 1"),
            ChapterBoundary(number=2, title="Cap 2", start_page=4, end_page=8, evidence="Cap 2"),
        ],
        notes="Teste de sobreposição",
    )
    with pytest.raises(ChapterDetectionError, match="sobrep"):
        validate_boundaries(mapping.chapters, total_pages=10, strict=True)


def test_out_of_range_pages_raise_error_on_strict_validation() -> None:
    mapping = ChapterMap(
        chapters=[
            ChapterBoundary(number=1, title="Cap 1", start_page=1, end_page=15, evidence="Cap 1"),
        ],
        notes="",
    )
    with pytest.raises(ChapterDetectionError, match="intervalo"):
        validate_boundaries(mapping.chapters, total_pages=10, strict=True)


def test_normalize_boundaries_records_warnings_for_overlap() -> None:
    mapping = ChapterMap(
        chapters=[
            ChapterBoundary(number=1, title="Cap 1", start_page=1, end_page=6, evidence="Cap 1"),
            ChapterBoundary(number=2, title="Cap 2", start_page=5, end_page=10, evidence="Cap 2"),
        ],
        notes="Nota original",
    )
    boundaries = normalize_boundaries(mapping, total_pages=10, expected=2)
    assert len(boundaries) == 2
    assert boundaries[0].end_page == 4
    assert boundaries[1].start_page == 5
    assert any("sobrep" in w.lower() for w in mapping.warnings)


def test_summarizer_chunks_long_chapter_and_filters_invalid_source_pages() -> None:
    fake_llm = FakeSummaryLLM()
    summarizer = ChapterSummarizer(llm=fake_llm, max_chars_per_chunk=2000)
    pages = [
        ExtractedPage(page_number=1, text="A" * 1500, char_count=1500),
        ExtractedPage(page_number=2, text="B" * 1500, char_count=1500),
    ]
    boundary = ChapterBoundary(number=1, title="Capítulo 1", start_page=1, end_page=2, evidence="A")

    summary = summarizer.summarize_pages(pages, boundary)
    # 2 chunks + 1 chamada de consolidação = 3 chamadas
    assert len(fake_llm.calls) >= 2
    assert summary.title == "Capítulo Teste"
    # Página 99 estava fora do intervalo 1..2 e deve ser filtrada das referências válidas
    assert 99 not in summary.source_pages
    assert set(summary.source_pages).issubset({1, 2})
