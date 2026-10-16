from __future__ import annotations

import re
from collections.abc import Sequence

from lcbooks.pdf.extractor import ExtractedPage
from lcbooks.schemas.chapters import ChapterBoundary, ChapterSummary

_PAGE_TAG_RE = re.compile(r"\[Página\s+(\d+)\]")


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    blocks = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for block in blocks:
        if current and current_len + len(block) + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = [block]
            current_len = len(block)
        else:
            if len(block) > max_chars:
                if current:
                    chunks.append("\n\n".join(current))
                    current = []
                    current_len = 0
                for start in range(0, len(block), max_chars):
                    chunks.append(block[start : start + max_chars])
            else:
                current.append(block)
                current_len += len(block) + 2

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _filter_valid_pages(pages: Sequence[int], start_page: int, end_page: int) -> list[int]:
    return sorted({p for p in pages if start_page <= p <= end_page})


class ChapterSummarizer:
    def __init__(self, llm, *, max_chars_per_chunk: int = 12000) -> None:
        self.llm = llm
        self.max_chars_per_chunk = max_chars_per_chunk

    def _sanitize_summary_pages(
        self, summary: ChapterSummary, boundary: ChapterBoundary
    ) -> ChapterSummary:
        valid_range_pages = list(range(boundary.start_page, boundary.end_page + 1))
        filtered_sources = _filter_valid_pages(
            summary.source_pages, boundary.start_page, boundary.end_page
        )
        if not filtered_sources:
            filtered_sources = valid_range_pages

        updated_claims = [
            c.model_copy(
                update={
                    "source_pages": _filter_valid_pages(
                        c.source_pages, boundary.start_page, boundary.end_page
                    )
                    or [boundary.start_page]
                }
            )
            for c in summary.supported_claims
        ]
        updated_defs = [
            d.model_copy(
                update={
                    "source_pages": _filter_valid_pages(
                        d.source_pages, boundary.start_page, boundary.end_page
                    )
                    or [boundary.start_page]
                }
            )
            for d in summary.definitions
        ]
        return summary.model_copy(
            update={
                "title": summary.title or boundary.title,
                "source_pages": filtered_sources,
                "supported_claims": updated_claims,
                "definitions": updated_defs,
            }
        )

    def summarize(self, chapter_text: str, boundary: ChapterBoundary) -> ChapterSummary:
        system_prompt = (
            "Você é um crítico literário acadêmico analisando uma obra. "
            "Mantenha um tom estritamente acadêmico, literário e analítico em português do Brasil. "
            "Atribua referências de páginas reais presentes no texto."
        )
        chunks = _split_into_chunks(chapter_text, self.max_chars_per_chunk)
        partial_summaries: list[ChapterSummary] = []

        for idx, chunk in enumerate(chunks, start=1):
            user_prompt = (
                f"Analise os temas, arcos e acontecimentos principais deste trecho ({idx}/{len(chunks)}).\n"
                f"Capítulo {boundary.number}: {boundary.title} (Páginas {boundary.start_page}-{boundary.end_page})\n\n"
                f"TEXTO DO CAPÍTULO:\n{chunk}"
            )
            part = self.llm.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=ChapterSummary,
            )
            if part is not None:
                partial_summaries.append(self._sanitize_summary_pages(part, boundary))

        if not partial_summaries:
            return ChapterSummary(
                title=boundary.title,
                thesis="Resumo narrativo do capítulo com foco nos acontecimentos e dilemas dos personagens.",
                key_points=["Desenvolvimento do enredo dramático."],
                examples=[],
                quotes_or_definitions=[],
                source_pages=list(range(boundary.start_page, boundary.end_page + 1)),
                audience_takeaway="Acompanhamento da trama e suspense.",
                visual_motifs=["Cenário dramático", "Elementos de mistério"],
            )

        if len(partial_summaries) == 1:
            return partial_summaries[0]

        consolidate_prompt = (
            f"Consolide os resumos parciais a seguir em um único resumo estruturado do "
            f"Capítulo {boundary.number}: {boundary.title} (Páginas {boundary.start_page}-{boundary.end_page}).\n\n"
            + "\n\n".join(s.model_dump_json(indent=2) for s in partial_summaries)
        )
        consolidated = self.llm.generate_structured(
            system_prompt=system_prompt,
            user_prompt=consolidate_prompt,
            schema=ChapterSummary,
        )
        if consolidated is not None:
            return self._sanitize_summary_pages(consolidated, boundary)

        first = partial_summaries[0]
        merged_points = list(dict.fromkeys(kp for s in partial_summaries for kp in s.key_points))
        merged_examples = list(dict.fromkeys(ex for s in partial_summaries for ex in s.examples))
        merged_motifs = list(dict.fromkeys(vm for s in partial_summaries for vm in s.visual_motifs))
        return self._sanitize_summary_pages(
            first.model_copy(
                update={
                    "key_points": merged_points,
                    "examples": merged_examples,
                    "visual_motifs": merged_motifs,
                }
            ),
            boundary,
        )

    def summarize_pages(
        self, pages: Sequence[ExtractedPage], boundary: ChapterBoundary
    ) -> ChapterSummary:
        selected = [
            f"[Página {p.page_number}]\n{p.text}"
            for p in pages
            if boundary.start_page <= p.page_number <= boundary.end_page
        ]
        return self.summarize("\n\n".join(selected), boundary)
