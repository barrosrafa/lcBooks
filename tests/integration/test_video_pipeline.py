from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from lcbooks.exceptions import TTSServiceError
from lcbooks.media.ffmpeg_renderer import FFmpegRenderer, RenderOptions
from lcbooks.media.quality import VideoQualityChecker
from lcbooks.media.scenes.pillow_provider import PillowSceneProvider
from lcbooks.media.tts.base import FakeTTSProvider
from lcbooks.pdf.chapter_detector import ChapterDetector
from lcbooks.pdf.extractor import PdfExtractor
from lcbooks.pipeline import build_parser
from lcbooks.schemas.chapters import (
    ChapterBoundary,
    ChapterMap,
    ChapterPackage,
    ChapterSummary,
    SupportedClaim,
)
from lcbooks.schemas.script import VideoScript
from lcbooks.services.book_pipeline import BookPipeline
from lcbooks.services.script_generator import ScriptGenerator
from lcbooks.services.storyboard import StoryboardGenerator
from lcbooks.services.summarizer import ChapterSummarizer
from lcbooks.services.video_pipeline import VideoPipeline
from lcbooks.storage.artifacts import ArtifactStore


class FakeBookLLM:
    def generate_structured(self, *, system_prompt: str, user_prompt: str, schema):
        if schema is ChapterMap:
            return ChapterMap(
                chapters=[
                    ChapterBoundary(
                        number=1,
                        title="Capítulo 1: A Origem",
                        start_page=1,
                        end_page=2,
                        evidence="Capítulo 1",
                    ),
                    ChapterBoundary(
                        number=2,
                        title="Capítulo 2: O Desfecho",
                        start_page=3,
                        end_page=4,
                        evidence="Capítulo 2",
                    ),
                ],
                notes="Mapa sintético de 2 capítulos",
            )
        if schema is ChapterSummary:
            return ChapterSummary(
                title="Resumo Sintético",
                thesis="Tese analítica baseada nas páginas do capítulo.",
                key_points=[
                    SupportedClaim(
                        claim="Ideia central apresentada no texto base.",
                        source_pages=[1, 2],
                        confidence="high",
                    )
                ],
                examples=[SupportedClaim(claim="Exemplo concreto.", source_pages=[2])],
                definitions=[SupportedClaim(claim="Conceito fundamental.", source_pages=[1])],
                audience_takeaway="Compreender a evolução narrativa.",
                visual_motifs=["Bússola dourada", "Horizonte noturno"],
            )
        if schema is VideoScript:
            dur = 3
            if "20 minuto" in user_prompt:
                dur = 20
            elif "1 minuto" in user_prompt:
                dur = 1
            return VideoScript(
                title=f"Roteiro de {dur} Minutos",
                duration_minutes=dur,
                target_audience="Leitores e estudantes",
                hook="Você já parou para pensar no que move esta história?",
                beats=["Introdução", "Desenvolvimento", "Conclusão"],
                narration=(
                    "Bem-vindos a mais uma análise literária aprofundada.\n\n"
                    "Neste capítulo exploramos os dilemas centrais com atenção aos detalhes.\n\n"
                    "Deixe seu comentário e inscreva-se para os próximos vídeos."
                ),
                cta="Inscreva-se e compartilhe!",
                b_roll_and_visuals=["Bússola sobre mapa antigo", "Páginas virando ao vento"],
            )
        # _ImageIdeas
        return schema(ideas=["Ilustração conceitual 1", "Ilustração conceitual 2"])


def test_end_to_end_book_pipeline_with_spaces_and_accents_in_path(
    tmp_path: Path,
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    pdf_path = make_pdf(
        [
            "Capítulo 1\nO início da jornada literária na página um.",
            "Continuação do primeiro capítulo na página dois.",
            "Capítulo 2\nO segundo capítulo começa na página três.",
            "Conclusão do livro na página quatro.",
        ],
        "livro_sintetico.pdf",
    )
    output_dir = tmp_path / "Saída de Vídeo — Edição Áéíóú"

    llm = FakeBookLLM()
    store = ArtifactStore(output_dir, job_id="job_e2e_test")
    renderer = FFmpegRenderer(
        options=RenderOptions(
            width=320,
            height=180,
            fps=15,
            crf=24,
            preset="ultrafast",
            burn_captions=True,
        )
    )
    video_pipeline = VideoPipeline(
        artifact_store=store,
        storyboard_generator=StoryboardGenerator(),
        scene_provider=PillowSceneProvider(width=320, height=180),
        tts_provider=FakeTTSProvider(seconds_per_word=0.12),
        renderer=renderer,
        quality_checker=VideoQualityChecker(expected_width=320, expected_height=180),
    )
    book_pipeline = BookPipeline(
        extractor=PdfExtractor(),
        chapter_detector=ChapterDetector(llm=llm),
        summarizer=ChapterSummarizer(llm=llm),
        script_generator=ScriptGenerator(llm=llm),
        video_pipeline=video_pipeline,
        artifact_store=store,
    )

    result = book_pipeline.run(
        pdf_path=pdf_path,
        output_dir=output_dir,
        expected_chapters=2,
        durations=[3, 1],
        render_videos=True,
        aspect_ratio="16:9",
        resume=True,
        continue_on_error=True,
    )

    assert result.failed_artifacts == []
    assert len(result.packages) == 2
    assert len(result.video_paths) == 4

    assert (output_dir / "book.json").is_file()
    assert (output_dir / "mapa_capitulos.json").is_file()
    assert (output_dir / "manifest.json").is_file()
    assert (output_dir / "resultado_completo.json").is_file()
    assert (output_dir / "roteiros_e_ideias.md").is_file()
    assert (output_dir / "run_report.json").is_file()
    assert (output_dir / "capitulo_01.json").is_file()
    assert (output_dir / "capitulo_02.json").is_file()

    vdir = output_dir / "chapter_01" / "video_03min"
    assert (vdir / "video.mp4").is_file()
    assert (vdir / "captions.srt").is_file()
    assert (vdir / "audio" / "narration.mp3").is_file()
    assert (vdir / "storyboard.json").is_file()
    assert (vdir / "quality_report.json").is_file()
    assert (vdir / "manifest.json").is_file()

    # Verifica que caminhos salvos no manifesto são relativos ao diretório de saída
    vmanifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
    assert vmanifest["status"] == "completed"
    assert not Path(vmanifest["video_path"]).is_absolute()
    assert vmanifest["video_path"] == "chapter_01/video_03min/video.mp4"


def test_failure_in_3min_script_preserves_20min_and_1min_videos(
    tmp_path: Path,
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    pdf_path = make_pdf(
        [
            "Capítulo 1\nConteúdo da primeira página.",
            "Segunda página do capítulo único.",
        ],
        "um_capitulo.pdf",
    )
    output_dir = tmp_path / "saida_isolamento"

    class SingleChapterLLM(FakeBookLLM):
        def generate_structured(self, *, system_prompt: str, user_prompt: str, schema):
            if schema is ChapterMap:
                return ChapterMap(
                    chapters=[
                        ChapterBoundary(
                            number=1,
                            title="Capítulo Único",
                            start_page=1,
                            end_page=2,
                            evidence="Capítulo 1",
                        )
                    ],
                    notes="",
                )
            return super().generate_structured(
                system_prompt=system_prompt, user_prompt=user_prompt, schema=schema
            )

    base_tts = FakeTTSProvider(seconds_per_word=0.10)

    class FailOn3MinTTS:
        def synthesize(self, text: str, output_path: Path) -> Path:
            if "video_03min" in output_path.as_posix():
                raise TTSServiceError("Falha simulada apenas no vídeo de 3 minutos", retryable=True)
            return base_tts.synthesize(text, output_path)

    llm = SingleChapterLLM()
    store = ArtifactStore(output_dir, job_id="job_isolation")
    renderer = FFmpegRenderer(
        options=RenderOptions(
            width=320,
            height=180,
            fps=15,
            preset="ultrafast",
            burn_captions=True,
        )
    )
    video_pipeline = VideoPipeline(
        artifact_store=store,
        storyboard_generator=StoryboardGenerator(),
        scene_provider=PillowSceneProvider(width=320, height=180),
        tts_provider=FailOn3MinTTS(),
        renderer=renderer,
        quality_checker=VideoQualityChecker(expected_width=320, expected_height=180),
    )
    book_pipeline = BookPipeline(
        extractor=PdfExtractor(),
        chapter_detector=ChapterDetector(llm=llm),
        summarizer=ChapterSummarizer(llm=llm),
        script_generator=ScriptGenerator(llm=llm),
        video_pipeline=video_pipeline,
        artifact_store=store,
    )

    result = book_pipeline.run(
        pdf_path=pdf_path,
        output_dir=output_dir,
        expected_chapters=1,
        durations=[20, 3, 1],
        render_videos=True,
        continue_on_error=True,
    )

    assert result.failed_artifacts == [(1, 3)]
    assert (output_dir / "chapter_01" / "video_20min" / "video.mp4").is_file()
    assert not (output_dir / "chapter_01" / "video_03min" / "video.mp4").exists()
    assert (output_dir / "chapter_01" / "video_01min" / "video.mp4").is_file()


def test_cli_parser_and_backward_compatibility() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "meu_livro.pdf",
            "--output",
            "./saida_teste",
            "--chapters",
            "12",
            "--durations",
            "20,3,1",
            "--aspect-ratio",
            "9:16",
            "--render-videos",
            "--resume",
            "--continue-on-error",
        ]
    )
    assert args.pdf == Path("meu_livro.pdf")
    assert args.output == Path("./saida_teste")
    assert args.chapters == 12
    assert args.durations == "20,3,1"
    assert args.aspect_ratio == "9:16"
    assert args.render_videos is True
    assert args.resume is True
    assert args.continue_on_error is True

    # Compatibilidade com JSON já gerado no repositório (se presente)
    legacy_json = Path(__file__).resolve().parents[2] / "saida_verity" / "capitulo_01.json"
    if legacy_json.is_file():
        pkg = ChapterPackage.model_validate_json(legacy_json.read_text(encoding="utf-8"))
        assert pkg.chapter_number == 1
        assert len(pkg.scripts) == 3


def test_google_cloud_tts_mocked_and_legacy_pipeline_helpers(tmp_path: Path) -> None:
    from lcbooks.media.tts.google_cloud import GoogleCloudTTS
    from lcbooks.pipeline import chapter_text, generate_package, write_markdown

    class DummyResponse:
        audio_content = b"ID3-fake-mp3-bytes-content"

    class DummyTTSClient:
        def synthesize_speech(self, *, input, voice, audio_config):
            assert input.text == "Texto sintetizado no Google Cloud."
            return DummyResponse()

    gtts = GoogleCloudTTS(
        language_code="pt-BR",
        voice_name="pt-BR-Standard-A",
        speaking_rate=1.0,
        client=DummyTTSClient(),
    )
    out_mp3 = gtts.synthesize("Texto sintetizado no Google Cloud.", tmp_path / "gcloud.mp3")
    assert out_mp3.is_file()
    assert out_mp3.read_bytes() == b"ID3-fake-mp3-bytes-content"

    pages = ["Página um do livro", "Página dois do livro"]
    text = chapter_text(pages, 1, 2)
    assert "[Página 1]" in text
    assert "[Página 2]" in text

    boundary = ChapterBoundary(
        number=1, title="Capítulo 1", start_page=1, end_page=2, evidence="Cap 1"
    )
    pkg = generate_package(FakeBookLLM(), boundary, text)
    assert pkg.chapter_number == 1
    assert len(pkg.scripts) == 3

    md_path = tmp_path / "roteiros_e_ideias.md"
    write_markdown([pkg], md_path)
    assert md_path.is_file()
    assert "## Capítulo 1: Capítulo 1" in md_path.read_text(encoding="utf-8")
