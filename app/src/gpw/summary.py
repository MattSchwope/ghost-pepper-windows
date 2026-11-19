"""Summaries from a prompt profile with the summary model (openai.summary_model).

The profile asks for a `Title:` line, then Markdown sections. Shared by meeting capture
(Phase 2d) and the long-recording inbox (Phase 4).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "Meeting"
MAX_TITLE_CHARS = 80


class TextModel(Protocol):
    def complete(self, instructions: str, text: str) -> str: ...


@dataclass(frozen=True)
class Summary:
    title: str
    body: str  # Markdown sections, headings at level 3 so they sit under the note's "## Summary"


def load_profile(prompt_dir: Path, profile: str) -> str:
    path = prompt_dir / f"{profile}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Summary profile not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def demote_headings(markdown: str, level: int = 3) -> str:
    """Push every heading to at least `level`, keeping their order of nesting."""
    lines = markdown.splitlines()
    found = [len(m.group(1)) for line in lines if (m := re.match(r"^(#{1,6})\s", line))]
    if not found:
        return markdown
    shift = max(0, level - min(found))
    if not shift:
        return markdown
    return "\n".join(
        re.sub(r"^(#{1,6})(\s)", lambda m: "#" * min(6, len(m.group(1)) + shift) + m.group(2), line)
        for line in lines
    )


def clean_title(title: str) -> str:
    title = re.sub(r"[*_`#]+", "", title).strip().strip('"').strip()
    title = " ".join(title.split())
    return title[:MAX_TITLE_CHARS].rstrip() or DEFAULT_TITLE


def parse_summary(output: str) -> Summary:
    """Split the model's answer into a title and the sections. Tolerates a `# Title` heading
    instead of the `Title:` line, or no title at all."""
    lines = output.strip().splitlines()
    title = ""
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        first = lines[0].strip()
        m = re.match(r"^\**title\**\s*:\s*(.+)$", first, re.IGNORECASE)
        h = re.match(r"^#\s+(.+)$", first)
        if m or h:
            title = (m or h).group(1)
            lines.pop(0)
    body = "\n".join(lines).strip()
    return Summary(clean_title(title), demote_headings(body))


def summarize(model: TextModel, instructions: str, transcript: str, context: str = "") -> Summary:
    """One call to the summary model. `context` is a few lines about the recording that go first."""
    started = time.perf_counter()
    text = f"{context.strip()}\n\n{transcript}" if context.strip() else transcript
    summary = parse_summary(model.complete(instructions, text))
    logger.info("Summary took=%.2fs chars_in=%s chars_out=%s", time.perf_counter() - started, len(text), len(summary.body))
    return summary
