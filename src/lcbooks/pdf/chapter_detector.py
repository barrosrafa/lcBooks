from __future__ import annotations

import re
import sys
from collections.abc import Sequence

from lcbooks.exceptions import ChapterDetectionError
from lcbooks.pdf.extractor import ExtractedPage
from lcbooks.schemas.chapters import ChapterBoundary, ChapterMap

_CHAPTER_PATTERN = re.compile(
    r"(?im)^\s*(cap[ií]tulo|chapter|parte)\s+[0-9ivxlc]+\b|^\s*[0-9]{1,2}[\.:\-]\s+.{3,100}$"
)


def _page_texts(pages: Sequence[ExtractedPage | str]) -> list[str]:
    return [p.text if isinstance(p, ExtractedPage) else str(p) for p in pages]


def likely_chapter_pages(pages: Sequence[ExtractedPage | str]) -> list[int]:
    texts = _page_texts(pages)
    candidates: list[int] = []
    for idx, text in enumerate(texts):
        first = "\n".join(text.splitlines()[:18])
        if _CHAPTER_PATTERN.search(first):
            candidates.append(idx + 1)
    return candidates


def page_samples(pages: Sequence[ExtractedPage | str], candidate_pages: list[int]) -> str:
    texts = _page_texts(pages)
    selected: set[int] = set()
    for p in candidate_pages:
        selected.update(range(max(1, p - 1), min(len(texts), p + 1) + 1))
    if not selected:
        selected = set(range(1, len(texts) + 1))
    chunks: list[str] = []
    for p in sorted(selected):
        excerpt = texts[p - 1][:1800]
        chunks.append(f"\n--- PÁGINA {p} ---\n{excerpt}")
    return "".join(chunks)


def validate_boundaries(
    chapters: Sequence[ChapterBoundary],
    total_pages: int,
    *,
    strict: bool = False,
) -> list[str]:
    """Valida limites de capítulos contra total de páginas, sobreposições, lacunas e duplicidades."""
    warnings: list[str] = []
    if not chapters:
        msg = "Nenhum capítulo foi identificado no mapa de capítulos."
        if strict:
            raise ChapterDetectionError(msg, retryable=False)
        warnings.append(msg)
        return warnings

    ordered = sorted(chapters, key=lambda c: (c.start_page, c.end_page))
    seen_titles: set[str] = set()

    for idx, ch in enumerate(ordered):
        if ch.start_page < 1 or ch.end_page > total_pages or ch.start_page > ch.end_page:
            msg = (
                f"Capítulo '{ch.title}' possui páginas fora do intervalo válido "
                f"(1..{total_pages}): {ch.start_page}-{ch.end_page}"
            )
            if strict:
                raise ChapterDetectionError(msg, retryable=False)
            warnings.append(msg)

        norm_title = ch.title.strip().lower()
        if norm_title and norm_title in seen_titles:
            warnings.append(f"Título de capítulo duplicado detectado: '{ch.title}'")
        seen_titles.add(norm_title)

        if idx + 1 < len(ordered):
            nxt = ordered[idx + 1]
            if ch.end_page >= nxt.start_page:
                msg = (
                    f"Intervalos sobrepostos entre '{ch.title}' ({ch.start_page}-{ch.end_page}) "
                    f"e '{nxt.title}' ({nxt.start_page}-{nxt.end_page})"
                )
                if strict:
                    raise ChapterDetectionError(msg, retryable=False)
                warnings.append(msg)
            elif nxt.start_page - ch.end_page > 1:
                warnings.append(
                    f"Lacuna de páginas entre '{ch.title}' (pág. {ch.end_page}) "
                    f"e '{nxt.title}' (pág. {nxt.start_page})"
                )

    return warnings


def normalize_boundaries(
    mapping: ChapterMap | None,
    total_pages: int,
    expected: int,
    *,
    strict: bool = False,
) -> list[ChapterBoundary]:
    if mapping is None or not mapping.chapters:
        if strict:
            raise ChapterDetectionError(
                "Mapa de capítulos vazio retornado pelo modelo.", retryable=True
            )
        fallback = ChapterBoundary(
            number=1,
            title="Capítulo 1",
            start_page=1,
            end_page=max(1, total_pages),
            evidence="Fallback integral do documento",
        )
        return [fallback]

    warnings = validate_boundaries(mapping.chapters, total_pages=total_pages, strict=strict)
    chapters = sorted(mapping.chapters, key=lambda c: c.start_page)
    if len(chapters) != expected:
        msg = f"Aviso: Gemini encontrou {len(chapters)} capítulos; esperado: {expected}."
        warnings.append(msg)
        print(msg, file=sys.stderr)

    mapping.warnings = list(dict.fromkeys([*(mapping.warnings or []), *warnings]))

    valid: list[ChapterBoundary] = []
    for i, ch in enumerate(chapters):
        start = max(1, min(total_pages, ch.start_page))
        natural_end = chapters[i + 1].start_page - 1 if i + 1 < len(chapters) else total_pages
        end = max(start, min(total_pages, ch.end_page, natural_end))
        valid.append(ch.model_copy(update={"number": i + 1, "start_page": start, "end_page": end}))
    return valid


class ChapterDetector:
    def __init__(self, llm, *, strict: bool = False) -> None:
        self.llm = llm
        self.strict = strict

    def detect_map(
        self, pages: Sequence[ExtractedPage | str], expected_chapters: int
    ) -> ChapterMap:
        candidates = likely_chapter_pages(pages)
        samples = page_samples(pages, candidates)
        system_prompt = (
            "Você é um bibliotecário/editor. Detecte os limites reais dos capítulos no texto fornecido. "
            "Responda em português. Use páginas começando em 1. Não crie capítulos inexistentes."
        )
        user_prompt = (
            f"Identifique exatamente os capítulos do livro (o usuário espera aproximadamente {expected_chapters}).\n"
            "Retorne título, primeira e última página de cada capítulo. Ignore sumário, prefácio e índice como "
            "capítulos, salvo se forem claramente capítulos do conteúdo.\n\n"
            f"AMOSTRAS DE PÁGINAS:\n{samples}"
        )
        mapping = self.llm.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=ChapterMap,
        )
        if mapping is None:
            if self.strict:
                raise ChapterDetectionError("Falha ao detectar capítulos via LLM.", retryable=True)
            mapping = ChapterMap(
                chapters=[
                    ChapterBoundary(
                        number=1,
                        title="Capítulo 1",
                        start_page=1,
                        end_page=len(pages),
                        evidence="Capítulo único de fallback",
                    )
                ],
                notes="Fallback gerado após falha na chamada estruturada.",
            )
        boundaries = normalize_boundaries(
            mapping, total_pages=len(pages), expected=expected_chapters, strict=self.strict
        )
        mapping.chapters = boundaries
        return mapping

    def detect(
        self, pages: Sequence[ExtractedPage | str], expected_chapters: int
    ) -> list[ChapterBoundary]:
        mapping = self.detect_map(pages, expected_chapters)
        return mapping.chapters
