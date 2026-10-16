from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from lcbooks.exceptions import PdfExtractionError


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    char_count: int


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(path: Path, *, max_bytes: int, max_pages: int) -> list[ExtractedPage]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"PDF não encontrado: {path}")

    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        max_mb = max(1, max_bytes // (1024 * 1024))
        raise ValueError(f"PDF excede o limite de {max_mb} MB ({size_bytes} bytes)")

    with path.open("rb") as fh:
        header = fh.read(5)
    if header != b"%PDF-":
        raise ValueError("Arquivo inválido: cabeçalho %PDF- não encontrado.")

    try:
        reader = PdfReader(str(path), strict=False)
    except (PdfReadError, Exception) as exc:
        raise ValueError(f"Falha ao ler estrutura do PDF: {exc}") from exc

    if reader.is_encrypted:
        raise ValueError(
            "PDF protegido por senha; forneça uma versão autorizada e descriptografada"
        )

    total_pages = len(reader.pages)
    if total_pages > max_pages:
        raise ValueError(f"PDF possui {total_pages} páginas; máximo configurado: {max_pages}")

    pages: list[ExtractedPage] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
        except Exception as exc:
            raise PdfExtractionError(
                f"Erro ao extrair texto da página {index}: {exc}", retryable=False
            ) from exc
        cleaned = clean_text(raw_text)
        pages.append(ExtractedPage(page_number=index, text=cleaned, char_count=len(cleaned)))

    if not any(page.char_count for page in pages):
        raise ValueError("Nenhum texto extraído. O documento pode precisar de OCR.")
    return pages


def read_pdf(
    path: Path,
    *,
    max_bytes: int = 100 * 1024 * 1024,
    max_pages: int = 1000,
) -> list[str]:
    """Compatibilidade com a assinatura original que retorna lista de strings por página."""
    extracted = extract_pdf(path, max_bytes=max_bytes, max_pages=max_pages)
    return [page.text for page in extracted]


class PdfExtractor:
    def __init__(self, *, max_bytes: int = 100 * 1024 * 1024, max_pages: int = 1000) -> None:
        self.max_bytes = max_bytes
        self.max_pages = max_pages

    def extract(self, path: Path) -> list[ExtractedPage]:
        return extract_pdf(path, max_bytes=self.max_bytes, max_pages=self.max_pages)

    @staticmethod
    def get_range(pages: list[ExtractedPage] | list[str], start: int, end: int) -> str:
        chunks: list[str] = []
        for page_num in range(start, end + 1):
            item = pages[page_num - 1]
            text = item.text if isinstance(item, ExtractedPage) else str(item)
            chunks.append(f"[Página {page_num}]\n{text}")
        return "\n\n".join(chunks)
