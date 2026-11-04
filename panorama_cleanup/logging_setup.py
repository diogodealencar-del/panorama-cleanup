"""Log de execução: um arquivo por run em logs/<run_id>.log (CLAUDE.md §14)."""

from __future__ import annotations

import logging
from pathlib import Path

from panorama_cleanup.config import Settings
from panorama_cleanup.safety import ensure_writable_target

LOGGER_NAME = "panorama_cleanup"
_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(settings: Settings, run_id: str) -> Path:
    log_file = ensure_writable_target(settings.logs_dir / f"{run_id}.log", settings)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(settings.log_level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(console)
    return log_file
