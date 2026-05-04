from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.safe_paths import resolve_safe_path
from app.tools.registry import ToolRegistry


def register_file_tools(registry: ToolRegistry, settings: Settings) -> None:
    def find_files(args: dict[str, Any]) -> dict[str, Any]:
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("Missing search pattern.")

        root_value = str(args.get("root", settings.workspace_root))
        root = resolve_safe_path(root_value, settings=settings, must_exist=True)
        if not root.is_dir():
            raise ValueError(f"Root path is not a directory: {root}")

        limit = int(args.get("limit", 50))
        matches: list[str] = []
        for path in root.rglob("*"):
            if path.is_file() and path.match(pattern):
                matches.append(str(path))
                if len(matches) >= limit:
                    break
        return {"count": len(matches), "matches": matches}

    def read_text_file(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("Missing file path.")
        max_chars = int(args.get("max_chars", 4000))
        resolved = resolve_safe_path(path, settings=settings, must_exist=True)
        content = resolved.read_text(encoding="utf-8", errors="replace")
        clipped = content[:max_chars]
        return {"path": str(resolved), "content": clipped, "truncated": len(content) > len(clipped)}

    def write_text_file(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        append = bool(args.get("append", False))
        if not path:
            raise ValueError("Missing file path.")
        resolved = resolve_safe_path(path, settings=settings, must_exist=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with resolved.open(mode=mode, encoding="utf-8") as file_handle:
            file_handle.write(content)
        return {"path": str(resolved), "bytes_written": len(content.encode("utf-8")), "append": append}

    def move_file(args: dict[str, Any]) -> dict[str, Any]:
        source = str(args.get("source", "")).strip()
        destination = str(args.get("destination", "")).strip()
        overwrite = bool(args.get("overwrite", False))
        if not source or not destination:
            raise ValueError("Both source and destination are required.")

        source_path = resolve_safe_path(source, settings=settings, must_exist=True)
        destination_path = resolve_safe_path(destination, settings=settings, must_exist=False)
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if destination_path.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {destination_path}")
        if destination_path.exists() and overwrite:
            if destination_path.is_dir():
                shutil.rmtree(destination_path)
            else:
                destination_path.unlink()

        shutil.move(str(source_path), str(destination_path))
        return {"source": str(source_path), "destination": str(destination_path), "moved": True}

    def delete_file(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("Missing file path.")
        resolved = resolve_safe_path(path, settings=settings, must_exist=True)
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        return {"path": str(resolved), "deleted": True}

    registry.register(
        name="find_files",
        description="Search files by glob pattern inside an allowed directory.",
        risk="low",
        parameters={"pattern": "string", "root": "string", "limit": "integer"},
        handler=find_files,
    )
    registry.register(
        name="read_text_file",
        description="Read text content from a file.",
        risk="low",
        parameters={"path": "string", "max_chars": "integer"},
        handler=read_text_file,
    )
    registry.register(
        name="write_text_file",
        description="Write or append text to a file.",
        risk="medium",
        parameters={"path": "string", "content": "string", "append": "boolean"},
        handler=write_text_file,
    )
    registry.register(
        name="move_file",
        description="Move/rename a file between allowed paths.",
        risk="medium",
        parameters={"source": "string", "destination": "string", "overwrite": "boolean"},
        handler=move_file,
    )
    registry.register(
        name="delete_file",
        description="Delete a file or directory.",
        risk="high",
        parameters={"path": "string"},
        handler=delete_file,
    )

