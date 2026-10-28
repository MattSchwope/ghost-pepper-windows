from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "ghost-pepper-windows.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    # Request lines from the HTTP client add noise without stage timings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
