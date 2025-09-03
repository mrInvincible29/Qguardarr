import json
import logging
from pathlib import Path
from typing import Dict, Optional


class DryRunStore:
    """Simple JSON-backed store for simulated per-torrent limits in dry-run."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._limits: Dict[str, int] = {}
        self._loaded = False
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Keys: torrent hash, Value: int limit
                        self._limits = {str(k): int(v) for k, v in data.items()}
                        self._loaded = True
            except Exception as e:
                logging.warning(f"DryRunStore load failed: {e}")
                self._limits = {}

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._limits, f, indent=2)
        except Exception as e:
            logging.warning(f"DryRunStore save failed: {e}")

    def get(self, torrent_hash: str) -> Optional[int]:
        return self._limits.get(torrent_hash)

    def set_many(self, limits: Dict[str, int]) -> int:
        if not limits:
            return 0
        self._limits.update(limits)
        self.save()
        return len(limits)

    def clear(self) -> None:
        self._limits.clear()
        self.save()
