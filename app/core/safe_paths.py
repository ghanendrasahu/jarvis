from __future__ import annotations

from pathlib import Path

from app.config import Settings


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_safe_path(path_value: str, settings: Settings, must_exist: bool = False) -> Path:
    raw_path = Path(path_value).expanduser()
    if not raw_path.is_absolute():
        raw_path = Path(settings.workspace_root) / raw_path
    resolved = raw_path.resolve()

    if not any(_is_within(resolved, root) for root in settings.resolved_allowed_roots):
        roots = ", ".join(str(root) for root in settings.resolved_allowed_roots)
        raise PermissionError(f"Path is outside allowed roots: {resolved}. Allowed roots: {roots}")

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Path does not exist: {resolved}")
    return resolved

