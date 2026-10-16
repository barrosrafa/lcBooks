from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from typing import Any

_API_KEY_PATTERN = re.compile(r"(AIza[0-9A-Za-z\-_]{16,}|ya29\.[0-9A-Za-z\-_]+)")


def sanitize_message(message: str, secrets: Iterable[str] = (), max_length: int = 2000) -> str:
    """Remove segredos conhecidos, tokens de API e trunca mensagens excessivas."""
    cleaned = _API_KEY_PATTERN.sub("[REDACTED_KEY]", message or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "...[truncated]"
    return cleaned


def configure_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger("lcbooks")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    job_id: str = "",
    chapter_number: int | None = None,
    script_duration: int | None = None,
    stage: str = "",
    level: int = logging.INFO,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {"event": event}
    if job_id:
        payload["job_id"] = job_id
    if chapter_number is not None:
        payload["chapter_number"] = chapter_number
    if script_duration is not None:
        payload["script_duration"] = script_duration
    if stage:
        payload["stage"] = stage
    for key, value in extra.items():
        if isinstance(value, str):
            payload[key] = sanitize_message(value, max_length=500)
        else:
            payload[key] = value
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))
