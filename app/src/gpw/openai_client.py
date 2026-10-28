"""OpenAI client setup. The API key comes from .env or the environment, never from source."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

    from .config import OpenAIConfig


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs into the environment. Existing variables win."""
    if not path.is_file():
        return
    for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)


def require_api_key(env_name: str) -> str:
    key = os.environ.get(env_name, "").strip()
    if not key or key == "sk-...":
        raise RuntimeError(f"{env_name} is not set. Add it to .env at the repo root (see .env.example).")
    return key


def make_client(cfg: OpenAIConfig) -> OpenAI:
    from openai import OpenAI

    return OpenAI(
        api_key=require_api_key(cfg.api_key_env),
        max_retries=cfg.max_retries,
        timeout=cfg.timeout_seconds,
    )
