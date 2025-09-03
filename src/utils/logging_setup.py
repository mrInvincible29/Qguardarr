import logging
from pathlib import Path
from typing import Optional


def setup_logging(level: str = "INFO", logfile: Optional[str] = None) -> bool:
    """Configure logging with optional file output.

    Ensures the log directory exists before creating the file handler.
    Returns True if file logging is enabled, else False.
    """
    handlers = [logging.StreamHandler()]
    file_enabled = False

    if logfile:
        try:
            log_path = Path(logfile)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(logfile))
            file_enabled = True
        except Exception:
            # Fall back to stream-only if file cannot be opened
            file_enabled = False

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )

    return file_enabled

