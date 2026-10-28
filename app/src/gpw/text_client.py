"""OpenAI text model client for cleanup, voice edits and summaries."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class OpenAITextClient:
    def __init__(self, client: Any, model: str, reasoning_effort: str | None = "none") -> None:
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort

    def complete(self, instructions: str, text: str) -> str:
        kwargs: dict[str, Any] = {"model": self.model, "instructions": instructions, "input": text}
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}

        started = time.perf_counter()
        response = self.client.responses.create(**kwargs)
        out = (response.output_text or "").strip()
        logger.info(
            "Text model=%s response=%.2fs chars_in=%s chars_out=%s",
            self.model, time.perf_counter() - started, len(text), len(out),
        )
        return out
