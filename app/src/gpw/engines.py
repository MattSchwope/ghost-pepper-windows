"""Speech-to-text engines. OpenAI GPT-Transcribe now; local Whisper returns in Phase 6."""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimedText:
    text: str
    api_seconds: float  # upload + OpenAI processing + download, as seen from here
    server_seconds: float | None  # OpenAI's own processing time, from the openai-processing-ms header

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

    def transcribe_clip(
        self,
        filename: str,
        data: bytes,
        keywords: list[str],
        languages: list[str],
        timeout: float,
        max_retries: int = 1,
    ) -> TimedText:
        """Send an in-memory dictation clip. Short timeout and few retries: someone is waiting to paste."""
        kwargs: dict[str, Any] = {"model": self.model}
        if keywords:
            kwargs["keywords"] = keywords
        if languages:
            kwargs["languages"] = languages
        client = self.client.with_options(timeout=timeout, max_retries=max_retries)
        started = time.perf_counter()
        raw = client.audio.transcriptions.with_raw_response.create(file=(filename, data), **kwargs)
        result = raw.parse()
        api_seconds = time.perf_counter() - started
        return TimedText((result.text or "").strip(), api_seconds, server_seconds(raw.headers))

    def prewarm(self) -> None:
        """Open the HTTPS connection ahead of the upload. A model list costs nothing."""
        self.client.with_options(timeout=5, max_retries=0).models.list()


def server_seconds(headers: Any) -> float | None:
    value = headers.get("openai-processing-ms") if headers is not None else None
    try:
        return round(float(value) / 1000, 3)
    except (TypeError, ValueError):
        return None


class DryRunEngine:
    """Stands in for OpenAI so hotkeys and paste can be tried for free. No network."""

    model = "dry-run"

    def transcribe_clip(self, filename: str, data: bytes, keywords: list[str], languages: list[str], **_: Any) -> TimedText:
        import soundfile as sf

        seconds = sf.info(io.BytesIO(data)).duration
        return TimedText(f"Dry run: heard {seconds:.1f} seconds of audio.", 0.0, None)

    def prewarm(self) -> None:
        pass
