from __future__ import annotations

from pathlib import Path

from .text_client import OpenAITextClient


def load_prompt(prompt_dir: Path, profile: str) -> str:
    path = prompt_dir / f"{profile}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt profile not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def clean_text(client: OpenAITextClient, raw_text: str, prompt_template: str) -> str:
    """Run the raw transcript through the text model with a prompt profile."""
    if not raw_text.strip():
        return ""
    return client.complete(prompt_template, raw_text)
