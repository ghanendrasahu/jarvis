from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]
ExecutionStatus = Literal["completed", "pending", "failed"]
CommandStatus = Literal["completed", "partial", "needs_approval", "failed"]


class Action(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = "low"
    reason: str = ""


class Plan(BaseModel):
    summary: str = ""
    actions: list[Action] = Field(default_factory=list)
    final_response: str = ""


class ExecuteCommandRequest(BaseModel):
    command: str
    source: str = "api"
    auto_approve: bool = False


class ToolExecutionResult(BaseModel):
    tool: str
    status: ExecutionStatus
    risk: RiskLevel = "low"
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    pending_approval_id: str | None = None


class ExecuteCommandResponse(BaseModel):
    command: str
    source: str
    status: CommandStatus
    response_text: str
    plan_summary: str = ""
    results: list[ToolExecutionResult] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ApprovalItem(BaseModel):
    approval_id: str
    command: str
    tool: str
    risk: RiskLevel
    reason: str
    created_at: str
    expires_at: str


class ExecuteApprovalResponse(BaseModel):
    status: str
    message: str
    execution: ExecuteCommandResponse | None = None

