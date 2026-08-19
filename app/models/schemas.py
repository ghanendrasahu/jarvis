from __future__ import annotations

from datetime import UTC, datetime
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
    """Unified contract every tool result must satisfy.

    ``output`` is the tool's payload (== the ``data`` half of the contract).
    ``metadata`` holds framework data (attempts, duration, risk, timeout) and
    ``requires_approval`` / ``approval_reason`` describe the permission decision.
    """

    tool: str
    status: ExecutionStatus
    risk: RiskLevel = "low"
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    pending_approval_id: str | None = None
    category: str = "general"
    success: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    approval_reason: str | None = None


class ExecuteCommandResponse(BaseModel):
    command: str
    source: str
    status: CommandStatus
    response_text: str
    plan_summary: str = ""
    results: list[ToolExecutionResult] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


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


class JobApplyRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    sites: list[str] | None = Field(default=None)
    max_applications: int | None = Field(default=None, ge=1, le=100)


class ScheduleCreateRequest(BaseModel):
    command: str = Field(min_length=1, max_length=500)
    recurrence: str = Field(min_length=1, max_length=100)


class JobApplication(BaseModel):
    created_at: str
    site: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    status: str
    notes: str | None = None

