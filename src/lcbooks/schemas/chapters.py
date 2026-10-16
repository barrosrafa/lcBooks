from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from lcbooks.schemas.script import VideoScript


class ChapterBoundary(BaseModel):
    number: int = Field(description="Número sequencial do capítulo")
    title: str = Field(description="Título do capítulo")
    start_page: int = Field(description="Página inicial do capítulo, começando em 1")
    end_page: int = Field(description="Página final do capítulo, inclusive")
    evidence: str = Field(default="", description="Trecho curto que comprova a identificação")


class ChapterMap(BaseModel):
    chapters: list[ChapterBoundary]
    notes: str = Field(
        default="", description="Observações sobre incertezas ou páginas preliminares"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Avisos de validação de intervalos"
    )


class SupportedClaim(BaseModel):
    claim: str
    source_pages: list[int] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


class ChapterSummary(BaseModel):
    title: str = Field(default="")
    thesis: str = Field(default="")
    key_points: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    quotes_or_definitions: list[str] = Field(default_factory=list)
    definitions: list[SupportedClaim] = Field(default_factory=list)
    supported_claims: list[SupportedClaim] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    audience_takeaway: str = Field(default="")
    visual_motifs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_claims(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        copied = dict(data)
        collected_claims: list[Any] = list(copied.get("supported_claims") or [])
        pages_seen: set[int] = set(copied.get("source_pages") or [])

        for field_name in ("key_points", "examples", "quotes_or_definitions"):
            raw_items = copied.get(field_name)
            if not isinstance(raw_items, list):
                continue
            str_items: list[str] = []
            for item in raw_items:
                if isinstance(item, SupportedClaim):
                    str_items.append(item.claim)
                    collected_claims.append(item)
                    pages_seen.update(item.source_pages)
                elif isinstance(item, dict) and "claim" in item:
                    str_items.append(str(item["claim"]))
                    collected_claims.append(item)
                    pages_seen.update(item.get("source_pages") or [])
                elif item is not None:
                    str_items.append(str(item))
            copied[field_name] = str_items

        raw_defs = copied.get("definitions")
        if isinstance(raw_defs, list):
            norm_defs: list[Any] = []
            for item in raw_defs:
                if isinstance(item, str):
                    norm_defs.append({"claim": item, "source_pages": sorted(pages_seen)})
                else:
                    norm_defs.append(item)
                    if isinstance(item, SupportedClaim):
                        pages_seen.update(item.source_pages)
                    elif isinstance(item, dict):
                        pages_seen.update(item.get("source_pages") or [])
            copied["definitions"] = norm_defs
            if not copied.get("quotes_or_definitions"):
                copied["quotes_or_definitions"] = [
                    d.claim if isinstance(d, SupportedClaim) else str(d.get("claim", ""))
                    for d in norm_defs
                    if (isinstance(d, SupportedClaim) and d.claim)
                    or (isinstance(d, dict) and d.get("claim"))
                ]

        if collected_claims and not copied.get("supported_claims"):
            copied["supported_claims"] = collected_claims
        if pages_seen and not copied.get("source_pages"):
            copied["source_pages"] = sorted(pages_seen)
        return copied


class ChapterPackage(BaseModel):
    chapter_number: int
    chapter_title: str
    page_range: str
    summary: ChapterSummary
    scripts: list[VideoScript]
    image_ideas: list[str] = Field(default_factory=list)
