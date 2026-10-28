"""Speech-to-text engines. OpenAI GPT-Transcribe now; local Whisper returns in Phase 6."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRICE_PER_MIN = {
    "gpt-transcribe": 0.0045,
    "gpt-live-transcribe": 0.017,
}

_BAD_KEYWORD_CHARS = set("<>\r\n")


def estimate_cost(model: str, minutes: float) -> float | None:
    rate = PRICE_PER_MIN.get(model)
    return None if rate is None else minutes * rate


def clean_keywords(raw: list[str]) -> list[str]:
    """Keywords must be single-line literals without < or > (the API rejects the request otherwise)."""
    out: list[str] = []
    for k in raw:
        k = "".join(ch for ch in k if ch not in _BAD_KEYWORD_CHARS).strip()
        if k and k not in out:
            out.append(k)
    return out


class OpenAIEngine:
    def __init__(self, client: Any, model: str = "gpt-transcribe") -> None:
        self.client = client
        self.model = model

    def transcribe(self, path: Path, prompt: str | None, keywords: list[str], languages: list[str]) -> str:
        kwargs: dict[str, Any] = {"model": self.model}
        if keywords:
            kwargs["keywords"] = keywords
        if languages:
            kwargs["languages"] = languages
        if prompt:
            kwargs["prompt"] = prompt

        started = time.perf_counter()
        with open(path, "rb") as f:
            result = self.client.audio.transcriptions.create(file=f, **kwargs)
        text = (result.text or "").strip()
        logger.info(
            "Transcribed model=%s upload_and_response=%.2fs chars=%s",
            self.model, time.perf_counter() - started, len(text),
        )
        return text
