from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

IMAGE_EXTS = {".png", ".gif"}


def list_background_images(directory: Path) -> list[dict[str, Any]]:
    """List static background images found in a directory (safe, name-only)."""
    if not directory.is_dir():
        return []
    files = sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    return [{"name": p.name, "url": "/backgrounds/" + quote(p.name)} for p in files]


def background_image_path(directory: Path, name: str) -> Path | None:
    """Resolve a background image filename without allowing path traversal."""
    safe = Path(name).name
    if safe != name:
        return None
    path = directory / safe
    return path if path.is_file() else None
