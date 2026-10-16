"""Converte um livro em PDF em capítulos, roteiros e vídeos MP4 usando LangChain + Gemini + FFmpeg.

Uso:
  lcbooks livro.pdf --output ./saida_livro --chapters 25 --durations 20,3,1 --render-videos

Variáveis de ambiente:
  GEMINI_API_KEY=...
  GEMINI_MODEL=gemini-2.5-flash (opcional)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from lcbooks.llm.client import LLMClient, make_llm
from lcbooks.pdf.chapter_detector import (
    likely_chapter_pages,
    normalize_boundaries,
    page_samples,
)
from lcbooks.pdf.extractor import PdfExtractor, clean_text, read_pdf
from lcbooks.schemas.chapters import (
    ChapterBoundary,
    ChapterMap,
    ChapterPackage,
    ChapterSummary,
)
from lcbooks.schemas.script import VideoScript
from lcbooks.services.book_pipeline import BookPipeline
from lcbooks.services.script_generator import ScriptGenerator
from lcbooks.services.summarizer import ChapterSummarizer
from lcbooks.storage.artifacts import ArtifactStore

__all__ = [
    "ChapterBoundary",
    "ChapterMap",
    "ChapterPackage",
    "ChapterSummary",
    "VideoScript",
    "build_parser",
    "chapter_text",
    "clean_text",
    "generate_package",
    "invoke_structured",
    "likely_chapter_pages",
    "main",
    "make_llm",
    "normalize_boundaries",
    "page_samples",
    "read_pdf",
    "write_markdown",
]


def chapter_text(pages: list[str], start: int, end: int) -> str:
    return PdfExtractor.get_range(pages, start, end)


def invoke_structured(
    llm,
    system: str,
    user: str,
    schema: type[BaseModel],
) -> BaseModel | None:
    if isinstance(llm, LLMClient):
        return llm.generate_structured(system_prompt=system, user_prompt=user, schema=schema)
    try:
        chain = llm.with_structured_output(schema)
        return chain.invoke([("system", system), ("human", user)])
    except Exception as exc:  # noqa: BLE001
        print(f"Aviso ao invocar modelo: {exc}", file=sys.stderr)
        return None


def generate_package(llm, boundary: ChapterBoundary, text: str) -> ChapterPackage:
    client = (
        llm
        if isinstance(llm, LLMClient)
        else LLMClient(raw_llm=llm, max_retries=1, retry_delay_seconds=0.0)
    )
    summarizer = ChapterSummarizer(llm=client)
    script_gen = ScriptGenerator(llm=client)
    summary = summarizer.summarize(text, boundary)
    scripts = [script_gen.generate(summary, boundary, duration_minutes=d) for d in (20, 3, 1)]
    ideas = script_gen.generate_image_ideas(summary, boundary)
    return ChapterPackage(
        chapter_number=boundary.number,
        chapter_title=boundary.title,
        page_range=f"{boundary.start_page}-{boundary.end_page}",
        summary=summary,
        scripts=scripts,
        image_ideas=ideas,
    )


def write_markdown(packages: list[ChapterPackage], output: Path) -> None:
    ArtifactStore._write_markdown(packages, output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Caminho para o arquivo PDF de entrada")
    parser.add_argument(
        "--output", type=Path, default=Path("saida_livro"), help="Diretório de saída"
    )
    parser.add_argument(
        "--chapters", type=int, default=25, help="Quantidade aproximada esperada de capítulos"
    )
    parser.add_argument("--model", default=None, help="Modelo Gemini (padrão via GEMINI_MODEL)")
    parser.add_argument(
        "--durations",
        default="20,3,1",
        help="Durações dos roteiros/vídeos separadas por vírgula (ex.: 20,3,1)",
    )
    parser.add_argument(
        "--aspect-ratio",
        choices=("16:9", "9:16"),
        default="16:9",
        help="Proporção do vídeo (16:9 horizontal ou 9:16 vertical)",
    )
    parser.add_argument(
        "--render-videos",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Renderiza o vídeo MP4 imediatamente após cada roteiro",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reutiliza artefatos válidos de execuções anteriores",
    )
    parser.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Continua processando os próximos roteiros em caso de erro isolado",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refaz todos os artefatos ignorando cache anterior",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Exibe logs estruturados detalhados em modo debug",
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    try:
        durations = [int(item.strip()) for item in args.durations.split(",") if item.strip()]
    except ValueError as exc:
        raise SystemExit("--durations aceita uma lista de 1,3,20, por exemplo 20,3,1") from exc

    if not durations or any(item not in {1, 3, 20} for item in durations):
        raise SystemExit("--durations aceita uma lista de 1,3,20, por exemplo 20,3,1")

    pipeline = BookPipeline.from_environment(
        model=args.model,
        render_videos=args.render_videos,
        verbose=args.verbose,
    )
    result = pipeline.run(
        pdf_path=args.pdf,
        output_dir=args.output,
        expected_chapters=args.chapters,
        durations=durations,
        render_videos=args.render_videos,
        aspect_ratio=args.aspect_ratio,
        resume=args.resume,
        continue_on_error=args.continue_on_error,
        force=args.force,
    )
    print(f"Concluído. Arquivos em: {args.output.resolve()}")
    if result.failed_artifacts:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
