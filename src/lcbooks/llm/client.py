from __future__ import annotations

import os
import sys
import time
from typing import TypeVar

from google.ai.generativelanguage_v1beta.types import HarmCategory, SafetySetting
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ValidationError

from lcbooks.exceptions import LLMServiceError
from lcbooks.logging_config import sanitize_message

T = TypeVar("T", bound=BaseModel)


def make_llm(model: str = "gemini-2.5-flash", temperature: float = 0.2, api_key: str | None = None):
    resolved_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not resolved_key:
        raise LLMServiceError(
            "Defina GEMINI_API_KEY no ambiente ou em um arquivo .env",
            retryable=False,
        )
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: SafetySetting.HarmBlockThreshold.BLOCK_NONE,
    }
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        google_api_key=resolved_key,
        safety_settings=safety_settings,
    )


class LLMClient:
    """Cliente centralizado para chamadas estruturadas ao Gemini com retry transitório."""

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.2,
        api_key: str | None = None,
        raw_llm=None,
        max_retries: int = 3,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._api_key = api_key
        self._raw_llm = raw_llm
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

    @property
    def raw_llm(self):
        if self._raw_llm is None:
            self._raw_llm = make_llm(
                model=self.model, temperature=self.temperature, api_key=self._api_key
            )
        return self._raw_llm

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
    ) -> T | None:
        total_attempts = max(1, self.max_retries + 1)
        for attempt in range(1, total_attempts + 1):
            try:
                chain = self.raw_llm.with_structured_output(schema)
                result = chain.invoke([("system", system_prompt), ("human", user_prompt)])
                if isinstance(result, schema):
                    return result
                if isinstance(result, dict):
                    return schema.model_validate(result)
                return result
            except ValidationError as exc:
                clean_err = sanitize_message(str(exc))
                print(
                    f"Aviso de validação estruturada (sem retry automático): {clean_err}",
                    file=sys.stderr,
                )
                return None
            except Exception as exc:  # noqa: BLE001
                clean_err = sanitize_message(str(exc))
                if attempt >= total_attempts:
                    print(
                        f"Aviso ao invocar modelo após {attempt} tentativa(s): {clean_err}",
                        file=sys.stderr,
                    )
                    return None
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds * attempt)
        return None
