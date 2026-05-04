from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from app.models.schemas import Action, ApprovalItem


@dataclass
class PendingApproval:
    approval_id: str
    command: str
    action: Action
    created_at: datetime
    expires_at: datetime


class ApprovalManager:
    def __init__(self, ttl_minutes: int = 30) -> None:
        self._ttl_minutes = ttl_minutes
        self._lock = Lock()
        self._pending: dict[str, PendingApproval] = {}

    def _cleanup_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired_ids = [key for key, item in self._pending.items() if item.expires_at <= now]
        for approval_id in expired_ids:
            self._pending.pop(approval_id, None)

    def create(self, command: str, action: Action) -> str:
        created_at = datetime.now(timezone.utc)
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
        return approval_id

    def approve(self, approval_id: str) -> PendingApproval | None:
        with self._lock:
            self._cleanup_expired()
            return self._pending.pop(approval_id, None)

    def reject(self, approval_id: str) -> bool:
        with self._lock:
            self._cleanup_expired()
            existed = approval_id in self._pending
            self._pending.pop(approval_id, None)
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

