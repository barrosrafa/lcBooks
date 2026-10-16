from __future__ import annotations

from pydantic import BaseModel

from lcbooks.llm.prompts import (
    DURATION_WORD_TARGETS,
    IMAGE_IDEAS_SYSTEM_PROMPT,
    SCRIPT_SYSTEM_PROMPT,
)
from lcbooks.schemas.chapters import ChapterBoundary, ChapterSummary
from lcbooks.schemas.script import VideoScript, ensure_script_segments


class _ImageIdeas(BaseModel):
    ideas: list[str]


class ScriptGenerator:
    def __init__(self, llm, *, words_per_minute: float = 140.0) -> None:
        self.llm = llm
        self.words_per_minute = words_per_minute

    def estimate_duration_minutes(self, narration: str) -> float:
        word_count = len((narration or "").split())
        return word_count / max(40.0, self.words_per_minute)

    def _is_within_word_tolerance(self, narration: str, duration_minutes: int) -> bool:
        words = len((narration or "").split())
        low, high = DURATION_WORD_TARGETS.get(duration_minutes, (100, 500))
        # Tolerância de ~25% abaixo do piso mínimo para disparar regeneração direcionada
        min_acceptable = max(25, int(low * 0.70))
        max_acceptable = int(high * 1.35)
        return min_acceptable <= words <= max_acceptable

    def _fallback_script(
        self, summary: ChapterSummary, boundary: ChapterBoundary, duration_minutes: int
    ) -> VideoScript:
        points_text = " ".join(summary.key_points) if summary.key_points else summary.thesis
        narration = (
            f"Bem-vindos à análise do Capítulo {boundary.number}: {boundary.title}.\n\n"
            f"{summary.thesis or 'Exploramos os acontecimentos centrais deste capítulo.'}\n\n"
            f"{points_text}\n\n"
            f"{summary.audience_takeaway or 'Acompanhe os próximos capítulos desta obra.'}"
        )
        script = VideoScript(
            title=f"{boundary.title} - Roteiro ({duration_minutes} min)",
            duration_minutes=duration_minutes,
            target_audience="Leitores e espectadores interessados em literatura",
            hook=f"Descubra o que realmente acontece em {boundary.title}.",
            beats=["Introdução e Gancho", "Desenvolvimento Central", "Conclusão e Reflexão"],
            narration=narration,
            cta="Curta o vídeo e inscreva-se para acompanhar os próximos capítulos.",
            b_roll_and_visuals=summary.visual_motifs or ["Imagens conceituais dramáticas"],
        )
        pages = summary.source_pages or list(range(boundary.start_page, boundary.end_page + 1))
        return ensure_script_segments(script, source_pages=pages)

    def generate(
        self,
        summary: ChapterSummary,
        boundary: ChapterBoundary,
        duration_minutes: int = 3,
    ) -> VideoScript:
        if duration_minutes not in {1, 3, 20}:
            raise ValueError("Durações suportadas no MVP: 1, 3 ou 20 minutos")

        low_words, high_words = DURATION_WORD_TARGETS[duration_minutes]
        base_prompt = (
            f"Crie um roteiro audiovisual estruturado de {duration_minutes} minuto(s) para o "
            f"Capítulo {boundary.number}: {boundary.title} (Páginas {boundary.start_page}-{boundary.end_page}).\n"
            f"Meta de extensão da narração: entre {low_words} e {high_words} palavras.\n"
            "Inclua título, público-alvo, gancho (hook), beats, narração dividida em parágrafos claros, "
            "segmentos numerados (seg_0001, seg_0002...), CTA e sugestões visuais.\n\n"
            f"RESUMO DE REFERÊNCIA:\n{summary.model_dump_json(indent=2)}"
        )

        result = self.llm.generate_structured(
            system_prompt=SCRIPT_SYSTEM_PROMPT,
            user_prompt=base_prompt,
            schema=VideoScript,
        )

        if result is not None and not self._is_within_word_tolerance(
            result.narration, duration_minutes
        ):
            current_words = len(result.narration.split())
            retry_prompt = (
                f"{base_prompt}\n\n"
                f"AJUSTE DE EXTENSÃO: a versão anterior teve {current_words} palavras, fora da faixa "
                f"esperada ({low_words}–{high_words} palavras para {duration_minutes} min). "
                "Reescreva ajustando o ritmo e a extensão da narração."
            )
            retried = self.llm.generate_structured(
                system_prompt=SCRIPT_SYSTEM_PROMPT,
                user_prompt=retry_prompt,
                schema=VideoScript,
            )
            if retried is not None:
                result = retried

        if result is None:
            return self._fallback_script(summary, boundary, duration_minutes)

        pages = summary.source_pages or list(range(boundary.start_page, boundary.end_page + 1))
        normalized = result.model_copy(update={"duration_minutes": duration_minutes})
        return ensure_script_segments(normalized, source_pages=pages)

    def generate_image_ideas(self, summary: ChapterSummary, boundary: ChapterBoundary) -> list[str]:
        user_prompt = (
            f"Crie 10 ideias de imagens para ilustrar o capítulo {boundary.number} — {boundary.title}.\n"
            "Cada ideia deve ser visualmente específica, sem texto ilegível na imagem, e incluir: "
            "assunto, composição, estilo, paleta e qual ponto da narrativa ela ilustra.\n"
            f"Use este resumo como base:\n{summary.model_dump_json(indent=2)}"
        )
        images = self.llm.generate_structured(
            system_prompt=IMAGE_IDEAS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=_ImageIdeas,
        )
        if images and getattr(images, "ideas", None):
            return images.ideas
        return summary.visual_motifs or ["Cena dramática em iluminação contrastada."]
