from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pypdf import PdfWriter


def build_synthetic_pdf(path: Path, page_texts: list[str]) -> Path:
    """Cria um PDF sintético válido com texto extraível pelo pypdf em cada página."""
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            # Adiciona stream de conteúdo PDF simples com fonte base Helvetica
            from pypdf.generic import (
                DecodedStreamObject,
                DictionaryObject,
                NameObject,
            )

            font_dict = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            resources = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})}
            )
            page[NameObject("/Resources")] = resources

            lines = text.splitlines() or [text]
            ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
            for line in lines:
                escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops.append(f"({escaped}) Tj T*")
            ops.append("ET")
            stream = DecodedStreamObject()
            stream.set_data("\n".join(ops).encode("latin-1", errors="replace"))
            page[NameObject("/Contents")] = stream

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


@pytest.fixture
def make_pdf(tmp_path: Path) -> Callable[[list[str], str], Path]:
    def _factory(page_texts: list[str], filename: str = "sample.pdf") -> Path:
        return build_synthetic_pdf(tmp_path / filename, page_texts)

    return _factory
