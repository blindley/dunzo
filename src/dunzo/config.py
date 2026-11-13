"""Settings, loaded from the environment (and .env at the project root).

Real environment variables take precedence over values in .env.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# src/<package>/config.py -> project root is three levels up from this file's directory
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to {PROJECT_ROOT / '.env'} (see .env.example)."
        )
    return value


def _resolve_data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    path = Path(raw).expanduser() if raw else Path("data")
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path

    @property
    def db_path(self) -> Path:
        """The SQLite file (content and progress tables live together in v1)."""
        return self.data_dir / "dunzo.db"

    @property
    def media_dir(self) -> Path:
        """Content-addressed media files: data/media/<sha256>."""
        return self.data_dir / "media"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(_require("PORT")),
        data_dir=_resolve_data_dir(),
    )
