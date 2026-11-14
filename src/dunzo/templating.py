"""Shared Jinja2 environment for the management UI."""

from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATES_DIR = PACKAGE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def filesize(num_bytes: int) -> str:
    """Format a byte count for people: 512 B, 1.5 KB, 3.2 MB."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} MB"  # unreachable; keeps type checkers happy


def note_preview(properties: dict, limit: int = 80) -> str:
    """A one-line summary of a note: its first non-empty text property, shortened."""
    for value in properties.values():
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text if len(text) <= limit else text[: limit - 1] + "…"
    return "(no text)"


GRADE_NAMES = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def grade_name(grade: int) -> str:
    return GRADE_NAMES.get(grade, str(grade))


def localtime(timestamp: float) -> str:
    """A unix timestamp as local date and time, e.g. 2026-11-14 09:30."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


templates.env.filters["filesize"] = filesize
templates.env.filters["note_preview"] = note_preview
templates.env.filters["grade_name"] = grade_name
templates.env.filters["localtime"] = localtime
