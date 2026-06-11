import sys
from pathlib import Path

from loguru import logger

from pathosphere.config import get_settings

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return

    settings = get_settings()
    log_dir: Path = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan> — {message}",
        colorize=True,
    )

    logger.add(
        log_dir / "pathosphere_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="30 days",
        compression="gz",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {name}:{function}:{line} — {message}",
        encoding="utf-8",
    )

    _configured = True
