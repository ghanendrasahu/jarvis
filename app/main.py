from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.security import enforce_rate_limit, verify_api_token
from app.bootstrap import Runtime, build_runtime
from app.models.schemas import (
    ExecuteApprovalResponse,
    ExecuteCommandRequest,
    ExecuteCommandResponse,
    JobApplyRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = get_runtime()
    app.state.runtime = runtime
    yield
    runtime.voice.stop()


app = FastAPI(
    title="JARVIS",
    version="1.1.0",
    description="Advanced JARVIS API for voice-first system, desktop, and autonomous agent automation.",
    lifespan=lifespan,
)

_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


# Bind early so /v1/health works even before lifespan completes in tests.
app.state.runtime = get_runtime()


def _configure_cors() -> None:
    settings = app.state.runtime.settings
    origins = [origin.strip() for origin in settings.cors_allowed_origins if origin.strip()]
    if not origins:
        origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=len(origins) == 1,
    )


_configure_cors()

_AUTH = Depends(verify_api_token)
_RATELIMIT = Depends(enforce_rate_limit)


@app.get("/v1/health")
def health() -> dict:
    runtime = app.state.runtime
    return {
        "status": "ok",
        "app": runtime.settings.app_name,
        "provider": runtime.settings.llm_provider,
        "voice_running": runtime.voice.is_running(),
    }


@app.get("/v1/tools", dependencies=[_AUTH, _RATELIMIT])
def tools() -> list[dict]:
    return app.state.runtime.registry.catalog()


@app.post("/v1/execute", response_model=ExecuteCommandResponse, dependencies=[_AUTH, _RATELIMIT])
def execute(request: ExecuteCommandRequest) -> ExecuteCommandResponse:
    return app.state.runtime.orchestrator.execute_command(
        command=request.command,
        source=request.source,
        auto_approve=request.auto_approve,
    )


@app.get("/v1/pending-approvals", dependencies=[_AUTH, _RATELIMIT])
def pending_approvals() -> list[dict]:
    return [item.model_dump() for item in app.state.runtime.orchestrator.list_pending_approvals()]


@app.post("/v1/approve/{approval_id}", response_model=ExecuteApprovalResponse, dependencies=[_AUTH, _RATELIMIT])
def approve(approval_id: str) -> ExecuteApprovalResponse:
    return app.state.runtime.orchestrator.approve(approval_id)


@app.get("/v1/history", dependencies=[_AUTH, _RATELIMIT])
def history(limit: int = 30) -> list[dict]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return app.state.runtime.orchestrator.get_history(limit=limit)


@app.get("/v1/history/tools", dependencies=[_AUTH, _RATELIMIT])
def tool_history(limit: int = 50) -> list[dict]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return app.state.runtime.orchestrator.get_tool_history(limit=limit)


@app.get("/v1/metrics", dependencies=[_AUTH, _RATELIMIT])
def metrics(limit: int = 200) -> dict:
    if limit < 10 or limit > 5000:
        raise HTTPException(status_code=400, detail="limit must be between 10 and 5000")
    return app.state.runtime.orchestrator.get_metrics(limit=limit)


@app.post("/v1/voice/start", dependencies=[_AUTH, _RATELIMIT])
def start_voice() -> dict:
    runtime = app.state.runtime
    if not runtime.settings.voice_enabled:
        raise HTTPException(status_code=400, detail="Voice mode is disabled by config.")
    started = runtime.voice.start()
    return {"status": "started" if started else "already_running"}


@app.post("/v1/voice/stop", dependencies=[_AUTH, _RATELIMIT])
def stop_voice() -> dict:
    stopped = app.state.runtime.voice.stop()
    return {"status": "stopped" if stopped else "already_stopped"}


@app.post("/v1/job/apply", response_model=ExecuteCommandResponse, dependencies=[_AUTH, _RATELIMIT])
def job_apply(request: JobApplyRequest) -> ExecuteCommandResponse:
    return app.state.runtime.orchestrator.execute_command(
        command=f"apply for {request.query}",
        source="api",
        auto_approve=True,
    )


@app.get("/v1/job/task", dependencies=[_AUTH, _RATELIMIT])
def job_task() -> dict:
    return app.state.runtime.job_agent.status()


@app.post("/v1/job/cancel", dependencies=[_AUTH, _RATELIMIT])
def job_cancel() -> dict:
    return app.state.runtime.job_agent.cancel()


@app.get("/v1/job/applied", dependencies=[_AUTH, _RATELIMIT])
def job_applied(limit: int = 50) -> dict:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    agent = app.state.runtime.job_agent
    return {"items": agent.applied(limit=limit), "totals": agent.applied_totals()}
