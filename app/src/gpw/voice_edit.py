"""Voice edit (Phase 3): rewrite the selected text with a spoken instruction.

The hotkey script copies the selection, records the instruction like a dictation, and sends both
to the service (POST /voice-edit). The service transcribes the instruction with the same path and
dictionary keywords as dictation. Then the text model (openai.text_model, prompt
app/prompts/voice_edit.md) returns only the rewritten text, which the script pastes over the
selection. Nothing here logs the selection, the instruction or the result.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Iterable, Protocol

from .cleanup import keep_term_case

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger(__name__)

PROMPT_PROFILE = "voice_edit"
LABEL = "[voice edit]"  # its row in the recent-dictations window
MAX_SPELLINGS = 200  # dictionary spellings listed in the prompt
RETRIES = 2  # OpenAI retries for one voice edit; the dictation client's 5 would keep the user waiting
NO_SELECTION = "Select text first, then hold the voice-edit keys and say the change."


def word_count(text: str) -> int:
    return len(text.split())


def selection_problem(selection: str, max_words: int) -> str:
    """Why this selection cannot be edited, for the tooltip; "" when it can."""
    if not selection.strip():
        return NO_SELECTION
    words = word_count(selection)
    if words > max_words:
        return f"The selection is {words:,} words. Voice edit takes up to {max_words:,}, so nothing was changed."
    return ""


def spellings(terms: Iterable[str], replace: dict[str, str]) -> list[str]:
    """Dictionary terms and the right-hand side of replace: fixes, once each, top of the list first."""
    seen: set[str] = set()
    out: list[str] = []
    for spelling in [*terms, *replace.values()]:
        spelling = spelling.strip()
        if spelling and spelling.casefold() not in seen:
            seen.add(spelling.casefold())
            out.append(spelling)
    return out[:MAX_SPELLINGS]


def build_instructions(prompt: str, keep: list[str]) -> str:
    if not keep:
        return prompt
    return prompt.rstrip() + "\n\nSpellings to keep:\n" + "\n".join(f"- {s}" for s in keep)


def build_input(selection: str, instruction: str) -> str:
    return f"<instruction>\n{instruction.strip()}\n</instruction>\n\n<selection>\n{selection}\n</selection>"


# What models put around an answer even when told not to.
_REWRITE_WORDS = (
    r"text|version|rewrite|rewritten|revised|revision|edit|edited|updated|shortened|shorter|longer|formal|"
    r"casual|bullets?|grammar|corrected|selection|result|paragraph"
)
_PREAMBLE_LINE = re.compile(
    rf"(?i)^(?:(?:sure|okay|ok|certainly|of course|absolutely)[,!.]?\s+)?"
    rf"(?:(?:here(?:'s|’s| is| are)|below is|this is)\b.{{0,100}}\b(?:{_REWRITE_WORDS})\b.{{0,60}}"
    rf"|(?:the |your )?(?:rewritten|revised|edited|updated|corrected|shortened)(?: text| version)?)[:.]$"
)
_ACK_LINE = re.compile(r"(?i)^(?:sure|okay|ok|certainly|of course|absolutely|done)[.!]?$")
_POSTAMBLE = re.compile(
    r"(?i)^(?:let me know|i hope|hope this|feel free|i(?: have|'ve|’ve) (?:kept|made|changed|shortened|rewritten|fixed|corrected|removed))\b"
)
_FENCE = re.compile(r"\A```[\w+-]*[ \t]*\n(.*?)\n?```\Z", re.DOTALL)
_TAGS = re.compile(r"(?is)\A<(selection|text|rewritten|result|output|answer)>\s*(.*?)\s*</\1>\Z")
_QUOTES = {'"': '"', "“": "”", "'": "'", "‘": "’", "«": "»"}


def strip_output(text: str, selection: str) -> str:
    """The model's answer without the wrapping it was told not to add: a preamble line, code
    fences, tags, quotes around the whole answer, or a closing remark. Anything the selection
    itself starts or ends with is left alone."""
    out = text.strip()
    sel = selection.strip()
    sel_folded = sel.casefold()
    while True:
        first, _, rest = out.partition("\n")
        line = first.strip()
        if rest.strip() and (_PREAMBLE_LINE.match(line) or _ACK_LINE.match(line)) and not sel_folded.startswith(line.casefold()):
            out = rest.strip()
            continue
        break
    m = _FENCE.match(out)
    if m and not sel.startswith("```"):
        out = m.group(1).strip()
    m = _TAGS.match(out)
    if m and not sel.startswith("<"):
        out = m.group(2).strip()
    paragraphs = out.rsplit("\n\n", 1)
    if len(paragraphs) == 2 and _POSTAMBLE.match(paragraphs[1].strip()) and paragraphs[1].strip().casefold() not in sel_folded:
        out = paragraphs[0].strip()
    if len(out) >= 2 and out[0] in _QUOTES and out[-1] == _QUOTES[out[0]] and not sel.startswith(out[0]):
        inner = out[1:-1]
        if out[0] not in inner and _QUOTES[out[0]] not in inner:
            out = inner.strip()
    return out


def keep_edges(selection: str, text: str) -> str:
    """Give the rewrite the selection's leading and trailing whitespace, so a selected line keeps its line break."""
    lead = selection[: len(selection) - len(selection.lstrip())]
    trail = selection[len(selection.rstrip()) :]
    return lead + text.strip() + trail


class Completer(Protocol):
    def complete(
        self, instructions: str, text: str, *, max_output_tokens: int | None = None, timeout: float | None = None
    ) -> str: ...


class EmptyRewrite(RuntimeError):
    """The text model answered with nothing usable."""


class VoiceEditor:
    def __init__(self, client: Completer, prompt: str, max_output_tokens: int, timeout: float) -> None:
        self.client = client
        self.prompt = prompt
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout

    def rewrite(
        self, selection: str, instruction: str, terms: Iterable[str] = (), replace: dict[str, str] | None = None
    ) -> str:
        """The selection rewritten per the instruction. Raises on a failed call or an empty answer."""
        replace = replace or {}
        terms = list(terms)
        answer = self.client.complete(
            build_instructions(self.prompt, spellings(terms, replace)),
            build_input(selection, instruction),
            max_output_tokens=self.max_output_tokens,
            timeout=self.timeout,
        )
        text = strip_output(answer or "", selection)
        if not text.strip():
            raise EmptyRewrite("The text model returned no text.")
        return keep_edges(selection, keep_term_case(text, terms, replace))


class DryRunEditor:
    """`gpw serve --dry-run`: no OpenAI call. The selection comes back in capitals, so the paste is visible."""

    def rewrite(
        self, selection: str, instruction: str, terms: Iterable[str] = (), replace: dict[str, str] | None = None
    ) -> str:
        return selection.upper()


def build_editor(config: AppConfig, dry_run: bool) -> VoiceEditor | DryRunEditor | None:
    """The voice editor for `gpw serve`; None when hotkeys.voice_edit_hold is off."""
    if not config.hotkeys.voice_edit_key:
        logger.info("Voice edit is off (hotkeys.voice_edit_hold is empty)")
        return None
    if dry_run:
        logger.info("Voice edit on: hold %s (dry run: the selection comes back in capitals)", config.hotkeys.voice_edit_hold)
        return DryRunEditor()
    from .cleaner import load_prompt
    from .config import PROMPT_DIR
    from .openai_client import make_client
    from .text_client import OpenAITextClient

    client = make_client(config.openai).with_options(max_retries=RETRIES)
    ve = config.voice_edit
    logger.info(
        "Voice edit on: hold %s model=%s max_words=%s max_output_tokens=%s timeout=%ss",
        config.hotkeys.voice_edit_hold, config.openai.text_model, ve.max_words, ve.max_output_tokens, ve.timeout_seconds,
    )
    return VoiceEditor(
        OpenAITextClient(client, config.openai.text_model, config.openai.text_reasoning),
        load_prompt(PROMPT_DIR, PROMPT_PROFILE), ve.max_output_tokens, ve.timeout_seconds,
    )
