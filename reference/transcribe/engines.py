"""Transcription engines: OpenAI (cloud) and faster-whisper (local)."""

from __future__ import annotations

import os
from pathlib import Path

PRICE_PER_MIN = {
    "gpt-transcribe": 0.0045,
    "gpt-4o-transcribe": 0.006,
    "gpt-4o-mini-transcribe": 0.003,
    "whisper-1": 0.006,
}

_BAD_KEYWORD_CHARS = set("<>\r\n")


def estimate_cost(model: str, minutes: float) -> float | None:
    rate = PRICE_PER_MIN.get(model)
    return None if rate is None else minutes * rate


def clean_keywords(raw: list[str]) -> list[str]:
    """Keywords must be single-line literals without < or > (API rejects the request otherwise)."""
    out: list[str] = []
    for k in raw:
        k = "".join(ch for ch in k if ch not in _BAD_KEYWORD_CHARS).strip()
        if k and k not in out:
            out.append(k)
    return out


class OpenAIEngine:
    def __init__(self, model: str = "gpt-transcribe"):
        from openai import OpenAI

        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is not set. Add it to .env (see .env.example).")
        self.client = OpenAI(max_retries=5, timeout=600)
        self.model = model
        # keywords and languages[] are GPT-Transcribe parameters; older models take language + prompt.
        self.new_api = model.startswith(("gpt-transcribe", "gpt-live-transcribe"))

    def transcribe(self, path: Path, prompt: str | None, keywords: list[str], languages: list[str]) -> str:
        kwargs: dict = {"model": self.model}
        extra: dict = {}
        if self.new_api:
            if keywords:
                extra["keywords"] = keywords
            if languages:
                extra["languages"] = languages
        else:
            if languages:
                kwargs["language"] = languages[0]
            if keywords:
                prompt = f"{prompt or ''} Terms: {', '.join(keywords)}".strip()
        if prompt:
            kwargs["prompt"] = prompt
        if extra:
            kwargs["extra_body"] = extra

        with open(path, "rb") as f:
            result = self.client.audio.transcriptions.create(file=f, **kwargs)
        return (result.text or "").strip()


class LocalEngine:
    """Runs Whisper on this computer. Needs: pip install -r requirements-local.txt"""

    def __init__(self, model: str = "large-v3-turbo", gpu: bool = False):
        try:
            import av
            from faster_whisper import WhisperModel
        except ImportError:
            raise SystemExit("Local engine needs: pip install -r requirements-local.txt")
        if int(av.__version__.split(".")[0]) >= 19:
            raise SystemExit(f'PyAV {av.__version__} breaks faster-whisper. Run: pip install "av<19"')

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        opts = {"device": "cuda", "compute_type": "float16"} if gpu else {
            "device": "cpu", "compute_type": "int8", "cpu_threads": os.cpu_count() or 4}
        try:
            self.model = WhisperModel(model, local_files_only=True, **opts)
        except Exception:
            print(f"  Downloading model '{model}' (one time, weights only)...")
            self.model = WhisperModel(model, **opts)

    def transcribe(self, wav: Path, prompt: str | None, keywords: list[str], languages: list[str]):
        """Yields (start, end, text) segments."""
        hint = " ".join(filter(None, [prompt, ", ".join(keywords)])) or None
        segments, _info = self.model.transcribe(
            str(wav),
            language=languages[0] if languages else None,
            initial_prompt=hint,
            vad_filter=True,
            beam_size=5,
        )
        for seg in segments:
            text = seg.text.strip()
            if text:
                yield seg.start, seg.end, text
