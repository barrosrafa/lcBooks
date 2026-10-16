#!/usr/bin/env python3
"""Converte um livro em PDF em capítulos, roteiros e ideias visuais usando LangChain + Gemini.

Uso:
  python pdf_to_video_pipeline.py livro.pdf --output ./saida --chapters 25

Variáveis de ambiente:
  GEMINI_API_KEY=...
  GEMINI_MODEL=gemini-2.5-flash (opcional)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pypdf import PdfReader
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from google.ai.generativelanguage_v1beta.types import HarmCategory, SafetySetting


class ChapterBoundary(BaseModel):
    number: int = Field(description="Número sequencial do capítulo")
    title: str = Field(description="Título do capítulo")
    start_page: int = Field(description="Página inicial do capítulo, começando em 1")
    end_page: int = Field(description="Página final do capítulo, inclusive")
    evidence: str = Field(description="Trecho curto que comprova a identificação")


class ChapterMap(BaseModel):
    chapters: list[ChapterBoundary]
    notes: str = Field(description="Observações sobre incertezas ou páginas preliminares")


class ChapterSummary(BaseModel):
    title: str
    thesis: str
    key_points: list[str]
    examples: list[str]
    quotes_or_definitions: list[str]
    audience_takeaway: str
    visual_motifs: list[str]


class VideoScript(BaseModel):
    title: str
    duration_minutes: int
    target_audience: str
    hook: str
    beats: list[str] = Field(description="Blocos narrativos em ordem")
    narration: str = Field(description="Texto narrado completo ou suficientemente detalhado")
    cta: str
    b_roll_and_visuals: list[str]


class ChapterPackage(BaseModel):
    chapter_number: int
    chapter_title: str
    page_range: str
    summary: ChapterSummary
    scripts: list[VideoScript]
    image_ideas: list[str]


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_pdf(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(clean_text(page.extract_text() or ""))
    if not any(pages):
        raise RuntimeError("Não foi possível extrair texto. O PDF pode ser escaneado; aplique OCR antes.")
    return pages


def likely_chapter_pages(pages: list[str]) -> list[int]:
    pattern = re.compile(r"(?im)^\s*(cap[ií]tulo|chapter|parte)\s+[0-9ivxlc]+\b|^\s*[0-9]{1,2}[\.:\-]\s+.{3,100}$")
    candidates = []
    for idx, text in enumerate(pages):
        first = "\n".join(text.splitlines()[:18])
        if pattern.search(first):
            candidates.append(idx + 1)
    return candidates


def page_samples(pages: list[str], candidate_pages: list[int]) -> str:
    # Inclui páginas candidatas e uma janela curta para o modelo também enxergar o contexto.
    selected = set()
    for p in candidate_pages:
        selected.update(range(max(1, p - 1), min(len(pages), p + 1) + 1))
    if not selected:
        selected = set(range(1, len(pages) + 1))
    chunks = []
    for p in sorted(selected):
        excerpt = pages[p - 1][:1800]
        chunks.append(f"\n--- PÁGINA {p} ---\n{excerpt}")
    return "".join(chunks)


def chapter_text(pages: list[str], start: int, end: int) -> str:
    return "\n\n".join(f"[Página {i}]\n{pages[i-1]}" for i in range(start, end + 1))


def make_llm(model: str, temperature: float = 0.2):
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Defina GEMINI_API_KEY no ambiente ou em um arquivo .env")
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
    }
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        google_api_key=api_key,
        safety_settings=safety_settings,
    )


def invoke_structured(llm, system: str, user: str, schema: type[BaseModel]) -> BaseModel | None:
    try:
        chain = llm.with_structured_output(schema)
        return chain.invoke([("system", system), ("human", user)])
    except Exception as e:
        print(f"Aviso ao invocar modelo: {e}", file=sys.stderr)
        return None


def normalize_boundaries(mapping: ChapterMap, total_pages: int, expected: int) -> list[ChapterBoundary]:
    chapters = sorted(mapping.chapters, key=lambda c: c.start_page)
    if len(chapters) != expected:
        print(f"Aviso: Gemini encontrou {len(chapters)} capítulos; esperado: {expected}.", file=sys.stderr)
    valid: list[ChapterBoundary] = []
    for i, ch in enumerate(chapters):
        start = max(1, min(total_pages, ch.start_page))
        natural_end = chapters[i + 1].start_page - 1 if i + 1 < len(chapters) else total_pages
        end = max(start, min(total_pages, ch.end_page, natural_end))
        valid.append(ch.model_copy(update={"number": i + 1, "start_page": start, "end_page": end}))
    return valid


def generate_package(llm, boundary: ChapterBoundary, text: str) -> ChapterPackage:
    summary_system = (
        "Você é um crítico literário e editor especialista analisando um romance de ficção dramática. "
        "Responda em português do Brasil com foco analítico e neutro sobre a narrativa. Seja fiel ao texto."
    )
    truncated_text = text[:35000] if len(text) > 35000 else text
    summary_user = f"""Analise o capítulo ficcional abaixo e produza um resumo estruturado.
Capítulo {boundary.number}: {boundary.title}

TEXTO DO CAPÍTULO:
{truncated_text}
"""
    summary = invoke_structured(llm, summary_system, summary_user, ChapterSummary)
    if summary is None:
        summary = ChapterSummary(
            title=boundary.title,
            thesis="Resumo narrativo do capítulo com foco nos acontecimentos principais.",
            key_points=["Desenvolvimento do enredo dramático."],
            examples=[],
            quotes_or_definitions=[],
            audience_takeaway="Acompanhamento da trama e suspense.",
            visual_motifs=["Cenário dramático", "Elementos de mistério"],
        )

    script_system = (
        "Você é roteirista e diretor de conteúdo audiovisual para literatura e storytelling dramático. "
        "Responda em português do Brasil, com linguagem envolvente para narração de resumo literário."
    )
    script_user = f"""Com base no resumo a seguir, crie EXATAMENTE três roteiros: um de 20 minutos, um de 3 minutos e um de 1 minuto.
Para 20 min, escreva uma estrutura detalhada com narração suficiente para orientar um vídeo completo (aprox. 2.400–3.000 palavras).
Para 3 min, seja direto (aprox. 350–450 palavras).
Para 1 min, seja conciso e forte (aprox. 120–160 palavras).
Inclua hook, beats, narração, CTA e sugestões de b-roll em cada roteiro.

RESUMO:
{summary.model_dump_json(indent=2)}
"""
    scripts_result = invoke_structured(llm, script_system, script_user, _Scripts)
    if scripts_result is None or not getattr(scripts_result, "scripts", None):
        scripts = [
            VideoScript(
                title=f"{boundary.title} - Roteiro Literário",
                duration_minutes=3,
                target_audience="Leitores e fãs de suspense",
                hook="Mergulhe nos segredos revelados neste capítulo emocionante.",
                beats=["Introdução", "Conflito", "Reviravolta"],
                narration=summary.thesis,
                cta="Acompanhe o próximo capítulo.",
                b_roll_and_visuals=["Imagens de suspense e tom dramático"],
            )
        ]
    else:
        scripts = scripts_result.scripts

    image_system = "Você é diretor de arte e especialista em prompts visuais para ilustrar narrativas de mistério e drama. Responda em português do Brasil."
    image_user = f"""Crie 10 ideias de imagens para ilustrar o capítulo {boundary.number} — {boundary.title}.
Cada ideia deve ser visualmente específica, sem texto ilegível na imagem, e incluir: assunto, composição, estilo, paleta e qual ponto da narrativa ela ilustra.
Use este resumo como base:
{summary.model_dump_json(indent=2)}"""
    images = invoke_structured(llm, image_system, image_user, _ImageIdeas)
    ideas = images.ideas if images and getattr(images, "ideas", None) else ["Cena misteriosa em iluminação dramática."]

    return ChapterPackage(
        chapter_number=boundary.number,
        chapter_title=boundary.title,
        page_range=f"{boundary.start_page}-{boundary.end_page}",
        summary=summary,
        scripts=scripts,
        image_ideas=ideas,
    )


class _Scripts(BaseModel):
    scripts: list[VideoScript]


class _ImageIdeas(BaseModel):
    ideas: list[str]


def write_markdown(packages: list[ChapterPackage], output: Path) -> None:
    lines = ["# Roteiros de vídeo por capítulo", "", "Gerado automaticamente com LangChain + Gemini.", ""]
    for p in packages:
        lines += [f"## Capítulo {p.chapter_number}: {p.chapter_title}", f"**Páginas:** {p.page_range}", "", "### Resumo", p.summary.thesis, ""]
        lines.append("**Pontos-chave:** " + "; ".join(p.summary.key_points))
        lines += ["", "### Roteiros"]
        for s in p.scripts:
            lines += [f"#### {s.title} ({s.duration_minutes} min)", f"**Público:** {s.target_audience}", f"**Hook:** {s.hook}", "", s.narration, "", f"**CTA:** {s.cta}", ""]
        lines += ["### Ideias de imagens", *[f"- {idea}" for idea in p.image_ideas], ""]
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, default=Path("saida_livro"))
    parser.add_argument("--chapters", type=int, default=25)
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    pages = read_pdf(args.pdf)
    candidates = likely_chapter_pages(pages)
    print(f"PDF lido: {len(pages)} páginas; candidatos locais: {len(candidates)}")
    llm = make_llm(args.model)
    map_system = "Você é um bibliotecário/editor. Detecte os limites reais dos capítulos no texto fornecido. Responda em português. Use páginas começando em 1. Não crie capítulos inexistentes."
    map_user = f"""Identifique exatamente os capítulos do livro (o usuário espera aproximadamente {args.chapters}).
Retorne título, primeira e última página de cada capítulo. Ignore sumário, prefácio e índice como capítulos, salvo se forem claramente capítulos do conteúdo.

AMOSTRAS DE PÁGINAS:
{page_samples(pages, candidates)}"""
    mapping = invoke_structured(llm, map_system, map_user, ChapterMap)
    boundaries = normalize_boundaries(mapping, len(pages), args.chapters)
    (args.output / "mapa_capitulos.json").write_text(json.dumps(mapping.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    packages: list[ChapterPackage] = []
    for n, boundary in enumerate(boundaries, 1):
        print(f"[{n}/{len(boundaries)}] Gerando capítulo {boundary.number}: {boundary.title}")
        text = chapter_text(pages, boundary.start_page, boundary.end_page)
        package = generate_package(llm, boundary, text)
        packages.append(package)
        (args.output / f"capitulo_{boundary.number:02d}.json").write_text(package.model_dump_json(indent=2), encoding="utf-8")

    write_markdown(packages, args.output / "roteiros_e_ideias.md")
    (args.output / "resultado_completo.json").write_text(json.dumps([p.model_dump() for p in packages], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Concluído. Arquivos em: {args.output.resolve()}")


if __name__ == "__main__":
    main()
