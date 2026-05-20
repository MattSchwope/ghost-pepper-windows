from __future__ import annotations

import logging
import pyperclip

logger = logging.getLogger(__name__)


def copy_text(text: str) -> None:
    pyperclip.copy(text)
    logger.info("Copied text to clipboard: chars=%s", len(text))
