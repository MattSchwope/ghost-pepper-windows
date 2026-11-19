"""OpenAI text model client for cleanup, voice edits and summaries."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class IncompleteResponse(RuntimeError):
    """The model stopped before it finished, such as at max_output_tokens. Its partial text is not used."""


class OpenAITextClient:
    def __init__(self, client: Any, model: str, reasoning_effort: str | None = "none") -> None:
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort

    def complete(
        self, instructions: str, text: str, *, max_output_tokens: int | None = None, timeout: float | None = None
    ) -> str:
        kwargs: dict[str, Any] = {"model": self.model, "instructions": instructions, "input": text}
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout

        started = time.perf_counter()
        response = self.client.responses.create(**kwargs)
        if max_output_tokens is not None and getattr(response, "status", None) == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None) or "unknown"
            logger.warning("Text model=%s stopped early (%s); its answer was not used", self.model, reason)
            raise IncompleteResponse(f"The text model stopped early ({reason}).")
        out = (response.output_text or "").strip()
        logger.info(
            "Text model=%s response=%.2fs chars_in=%s chars_out=%s",
            self.model, time.perf_counter() - started, len(text), len(out),
        )
        return out
