from __future__ import annotations

from pathlib import Path
import logging

import requests

logger = logging.getLogger(__name__)


def load_prompt(prompt_dir: Path, profile: str) -> str:
    path = prompt_dir / f"{profile}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt profile not found: {path}")
    return path.read_text(encoding="utf-8").strip()


class OllamaCleaner:
    def __init__(self, url: str, model: str, timeout_seconds: int = 120) -> None:
        self.url = url
        self.model = model
        self.timeout_seconds = timeout_seconds

    def clean(self, raw_text: str, prompt_template: str) -> str:
        if not raw_text.strip():
            return ""

        prompt = f"{prompt_template}\n\nRaw transcript:\n{raw_text}"

        logger.info("Calling Ollama cleanup model=%s chars=%s", self.model, len(raw_text))
        response = requests.post(
            self.url,
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        cleaned = response.json()["message"]["content"].strip()
        logger.info("Cleanup complete: chars=%s", len(cleaned))
        return cleaned
