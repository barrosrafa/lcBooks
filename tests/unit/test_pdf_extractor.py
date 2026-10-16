from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from lcbooks.pdf.extractor import ExtractedPage, PdfExtractor, clean_text, extract_pdf


def test_missing_pdf_raises_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "inexistente.pdf"
    with pytest.raises(FileNotFoundError, match="PDF não encontrado"):
        extract_pdf(missing, max_bytes=10 * 1024 * 1024, max_pages=100)


def test_oversized_pdf_is_rejected(make_pdf: Callable[[list[str], str], Path]) -> None:
    pdf_path = make_pdf(["Capítulo 1\nTexto de teste"], "grande.pdf")
    with pytest.raises(ValueError, match="excede o limite"):
        extract_pdf(pdf_path, max_bytes=10, max_pages=100)


def test_non_pdf_file_is_rejected(tmp_path: Path) -> None:
    fake_file = tmp_path / "falso.pdf"
    fake_file.write_text("Isto não é um PDF válido", encoding="utf-8")
    with pytest.raises(ValueError, match="PDF"):
        extract_pdf(fake_file, max_bytes=10 * 1024 * 1024, max_pages=100)


def test_pdf_without_text_suggests_ocr(make_pdf: Callable[[list[str], str], Path]) -> None:
    blank_pdf = make_pdf(["", ""], "escaneado.pdf")
    with pytest.raises(ValueError, match="OCR"):
        extract_pdf(blank_pdf, max_bytes=10 * 1024 * 1024, max_pages=100)


def test_pdf_exceeding_max_pages_is_rejected(make_pdf: Callable[[list[str], str], Path]) -> None:
    pdf_path = make_pdf(["Página 1", "Página 2", "Página 3"], "muitas_paginas.pdf")
    with pytest.raises(ValueError, match="máximo configurado"):
        extract_pdf(pdf_path, max_bytes=10 * 1024 * 1024, max_pages=2)


def test_valid_pdf_extracts_pages_and_range(make_pdf: Callable[[list[str], str], Path]) -> None:
    pdf_path = make_pdf(
        ["Capítulo 1\nIntrodução da obra.", "Desenvolvimento do capítulo um."],
        "valido.pdf",
    )
    extractor = PdfExtractor(max_bytes=10 * 1024 * 1024, max_pages=50)
    pages = extractor.extract(pdf_path)

    assert len(pages) == 2
    assert isinstance(pages[0], ExtractedPage)
    assert pages[0].page_number == 1
    assert "Capítulo 1" in pages[0].text
    assert pages[0].char_count == len(pages[0].text)

    range_text = extractor.get_range(pages, 1, 2)
    assert "[Página 1]" in range_text
    assert "[Página 2]" in range_text
    assert clean_text("Texto\x00   com   espaços\n\n\n\nfim") == "Texto com espaços\n\nfim"
