from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from uuid import uuid4

from app.models.schemas import Action, ApprovalItem

_STATE_KEY = "pending_approvals"


@dataclass
class PendingApproval:
    approval_id: str
    command: str
    action: Action
    created_at: datetime
    expires_at: datetime


class ApprovalManager:
    def __init__(self, ttl_minutes: int = 30, memory: Any = None) -> None:
        self._ttl_minutes = ttl_minutes
        self._lock = Lock()
        self._memory = memory
        self._pending: dict[str, PendingApproval] = {}
        if memory is not None:
            self._load()

    def _load(self) -> None:
        raw = self._memory.get_state(_STATE_KEY, default={}) or {}
        for approval_id, payload in raw.items():
            try:
                item = PendingApproval(
                    approval_id=str(payload["approval_id"]),
                    command=str(payload.get("command") or ""),
                    action=Action.model_validate(payload.get("action") or {}),
                    created_at=datetime.fromisoformat(payload["created_at"]),
                    expires_at=datetime.fromisoformat(payload["expires_at"]),
                )
            except Exception:
                continue
            self._pending[approval_id] = item
        self._cleanup_expired()

    def _persist(self) -> None:
        if self._memory is None:
            return
        payload = {
            approval_id: {
                "approval_id": item.approval_id,
                "command": item.command,
                "action": item.action.model_dump(mode="json"),
                "created_at": item.created_at.isoformat(),
                "expires_at": item.expires_at.isoformat(),
            }
            for approval_id, item in self._pending.items()
        }
        self._memory.set_state(_STATE_KEY, payload)

    def _cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        expired_ids = [key for key, item in self._pending.items() if item.expires_at <= now]
        for approval_id in expired_ids:
            self._pending.pop(approval_id, None)

    def create(self, command: str, action: Action) -> str:
        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(minutes=self._ttl_minutes)
        approval_id = str(uuid4())[:8]
        pending = PendingApproval(
            approval_id=approval_id,
            command=command,
            action=action,
            created_at=created_at,
            expires_at=expires_at,
        )
        with self._lock:
            self._cleanup_expired()
            self._pending[approval_id] = pending
            self._persist()
        return approval_id

    def approve(self, approval_id: str) -> PendingApproval | None:
        with self._lock:
            self._cleanup_expired()
            item = self._pending.pop(approval_id, None)
            self._persist()
            return item

    def reject(self, approval_id: str) -> bool:
        with self._lock:
            self._cleanup_expired()
            existed = approval_id in self._pending
            self._pending.pop(approval_id, None)
            self._persist()
            return existed

    def list_pending(self) -> list[ApprovalItem]:
        with self._lock:
            self._cleanup_expired()
            items = sorted(self._pending.values(), key=lambda item: item.created_at, reverse=True)
            return [
                ApprovalItem(
                    approval_id=item.approval_id,
                    command=item.command,
                    tool=item.action.tool,
                    risk=item.action.risk,
                    reason=item.action.reason,
                    created_at=item.created_at.isoformat(),
                    expires_at=item.expires_at.isoformat(),
                )
                for item in items
            ]

