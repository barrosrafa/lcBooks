from __future__ import annotations

from pathlib import Path

from google.cloud import texttospeech

from lcbooks.exceptions import TTSServiceError
from lcbooks.logging_config import sanitize_message


class GoogleCloudTTS:
    def __init__(
        self,
        language_code: str = "pt-BR",
        voice_name: str = "pt-BR-Standard-A",
        speaking_rate: float = 1.0,
        client=None,
    ) -> None:
        self._client = client
        self.language_code = language_code
        self.voice_name = voice_name
        self.speaking_rate = speaking_rate

    @property
    def client(self):
        if self._client is None:
            try:
                self._client = texttospeech.TextToSpeechClient()
            except Exception as exc:
                raise TTSServiceError(
                    "Falha ao inicializar Google Cloud Text-to-Speech. Configure Application Default "
                    f"Credentials (GOOGLE_APPLICATION_CREDENTIALS): {sanitize_message(str(exc))}",
                    retryable=False,
                ) from exc
        return self._client

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not text or not text.strip():
            raise ValueError("Não é permitido sintetizar um segmento vazio")

        try:
            response = self.client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=text[:4800]),
                voice=texttospeech.VoiceSelectionParams(
                    language_code=self.language_code,
                    name=self.voice_name,
                ),
                audio_config=texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.MP3,
                    speaking_rate=self.speaking_rate,
                ),
            )
        except Exception as exc:
            raise TTSServiceError(
                f"Erro na chamada Google Cloud TTS: {sanitize_message(str(exc))}",
                retryable=True,
            ) from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.mp3")
        temporary.write_bytes(response.audio_content)
        temporary.replace(output_path)
        return output_path
