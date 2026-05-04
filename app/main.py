from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import build_runtime
from app.models.schemas import ExecuteApprovalResponse, ExecuteCommandRequest, ExecuteCommandResponse

runtime = build_runtime()
app = FastAPI(
    title=runtime.settings.app_name,
    version="1.0.0",
    description="Advanced JARVIS API for voice-first system and application automation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.get("/v1/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": runtime.settings.app_name,
        "provider": runtime.settings.llm_provider,
        "voice_running": runtime.voice.is_running(),
    }


@app.get("/v1/tools")
def tools() -> list[dict]:
    return runtime.registry.catalog()


@app.post("/v1/execute", response_model=ExecuteCommandResponse)
def execute(request: ExecuteCommandRequest) -> ExecuteCommandResponse:
    return runtime.orchestrator.execute_command(
        command=request.command,
        source=request.source,
        auto_approve=request.auto_approve,
    )


@app.get("/v1/pending-approvals")
def pending_approvals() -> list[dict]:
    return [item.model_dump() for item in runtime.orchestrator.list_pending_approvals()]


@app.post("/v1/approve/{approval_id}", response_model=ExecuteApprovalResponse)
def approve(approval_id: str) -> ExecuteApprovalResponse:
    return runtime.orchestrator.approve(approval_id)


@app.get("/v1/history")
def history(limit: int = 30) -> list[dict]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return runtime.orchestrator.get_history(limit=limit)


@app.get("/v1/history/tools")
def tool_history(limit: int = 50) -> list[dict]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return runtime.orchestrator.get_tool_history(limit=limit)


@app.get("/v1/metrics")
def metrics(limit: int = 200) -> dict:
    if limit < 10 or limit > 5000:
        raise HTTPException(status_code=400, detail="limit must be between 10 and 5000")
    return runtime.orchestrator.get_metrics(limit=limit)


@app.post("/v1/voice/start")
def start_voice() -> dict:
    if not runtime.settings.voice_enabled:
        raise HTTPException(status_code=400, detail="Voice mode is disabled by config.")
    started = runtime.voice.start()
    return {"status": "started" if started else "already_running"}


@app.post("/v1/voice/stop")
def stop_voice() -> dict:
    stopped = runtime.voice.stop()
    return {"status": "stopped" if stopped else "already_stopped"}


@app.on_event("shutdown")
def shutdown() -> None:
    runtime.voice.stop()
