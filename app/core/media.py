from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".gif"}

MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".gif": "image/gif",
}


def list_backgrounds(directory: Path) -> list[dict[str, Any]]:
    """List playable background videos found in a directory (safe, name-only)."""
    if not directory.is_dir():
        return []
    files = sorted(p for p in directory.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    return [{"name": p.name, "url": "/backgrounds/" + quote(p.name)} for p in files]


def background_path(directory: Path, name: str) -> Path | None:
    """Resolve a background filename without allowing path traversal."""
    safe = Path(name).name
    if safe != name:
        return None
    path = directory / safe
    return path if path.is_file() else None
