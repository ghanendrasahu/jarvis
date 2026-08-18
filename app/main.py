from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.security import enforce_rate_limit, verify_api_token
from app.bootstrap import Runtime, build_runtime
from app.core.events import EventHub
from app.core.media import background_path, list_backgrounds
from app.core.resources import collect_resources
from app.models.schemas import (
    ExecuteApprovalResponse,
    ExecuteCommandRequest,
    ExecuteCommandResponse,
    JobApplyRequest,
    ScheduleCreateRequest,
)

_web_dir = Path(__file__).resolve().parent / "web"
_backgrounds_dir = Path(__file__).resolve().parent.parent / "data" / "backgrounds"


def create_app(runtime: Runtime | None = None, hub: EventHub | None = None, manage_lifecycle: bool = True) -> FastAPI:
    """Build the JARVIS web API. Can be bound to a caller-owned runtime + event hub.

    - ``runtime=None`` builds a fresh runtime (standalone server).
    - ``hub`` lets an embedded client share live events with the web UI.
    - ``manage_lifecycle=False`` keeps scheduler/voice lifecycle owned by the caller
      (used when the Tkinter HUD embeds this server in-process).
    """
    events = hub or EventHub()
    app_runtime = runtime if runtime is not None else build_runtime(events=events)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage_lifecycle:
            def _on_scheduler_run(job: dict, status: str, text: str) -> None:
                events.publish(
                    "scheduler_run",
                    {"command": job.get("command"), "status": status, "result": text, "job_id": job.get("id")},
                )

            app_runtime.scheduler.on_run = _on_scheduler_run
            app_runtime.scheduler.start()
        yield
        if manage_lifecycle:
            app_runtime.scheduler.stop()
            app_runtime.voice.stop()

    app = FastAPI(
        title="JARVIS",
        version="1.2.0",
        description="Advanced JARVIS API for voice-first system, desktop, and autonomous agent automation.",
        lifespan=lifespan,
    )
    app.state.runtime = app_runtime
    app.state.hub = events

    def _configure_cors() -> None:
        settings = app_runtime.settings
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

    @app.post("/v1/reject/{approval_id}", response_model=ExecuteApprovalResponse, dependencies=[_AUTH, _RATELIMIT])
    def reject(approval_id: str) -> ExecuteApprovalResponse:
        return app.state.runtime.orchestrator.reject(approval_id)

    @app.get("/v1/schedules", dependencies=[_AUTH, _RATELIMIT])
    def schedules_list() -> list[dict]:
        return app.state.runtime.scheduler.list_jobs()

    @app.post("/v1/schedules", dependencies=[_AUTH, _RATELIMIT])
    def schedules_create(request: ScheduleCreateRequest) -> dict:
        return app.state.runtime.scheduler.add(request.command, request.recurrence)

    @app.delete("/v1/schedules/{job_id}", dependencies=[_AUTH, _RATELIMIT])
    def schedules_delete(job_id: str) -> dict:
        if not app.state.runtime.scheduler.remove(job_id):
            raise HTTPException(status_code=404, detail=f"No schedule with id '{job_id}'.")
        return {"ok": True}

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

    @app.post("/v1/job/resume", dependencies=[_AUTH, _RATELIMIT])
    def job_resume() -> dict:
        return app.state.runtime.job_agent.resume()

    @app.get("/v1/job/applied", dependencies=[_AUTH, _RATELIMIT])
    def job_applied(limit: int = 50) -> dict:
        if limit < 1 or limit > 200:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
        agent = app.state.runtime.job_agent
        return {"items": agent.applied(limit=limit), "totals": agent.applied_totals()}

    @app.get("/v1/resources", dependencies=[_AUTH, _RATELIMIT])
    def resources() -> dict:
        return collect_resources()

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        runtime = app.state.runtime
        hub = app.state.hub
        expected = (runtime.settings.api_token or "").strip()
        token = websocket.query_params.get("token", "")
        if expected and token != expected:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        outbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        loop = asyncio.get_running_loop()

        def _publish(topic: str, payload: dict) -> None:
            def _put() -> None:
                try:
                    outbox.put_nowait({"topic": topic, "payload": payload})
                except asyncio.QueueFull:
                    pass

            loop.call_soon_threadsafe(_put)

        unsubscribe = hub.subscribe(_publish)

        async def _reader() -> None:
            try:
                while True:
                    await websocket.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                pass

        reader = asyncio.create_task(_reader())
        try:
            while True:
                msg = await outbox.get()
                await websocket.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader.cancel()
            unsubscribe()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_web_dir / "index.html")

    @app.get("/v1/backgrounds", dependencies=[_AUTH, _RATELIMIT])
    def backgrounds() -> list[dict]:
        return list_backgrounds(_backgrounds_dir)

    @app.get("/backgrounds/{name}")
    def background_file(name: str) -> FileResponse:
        path = background_path(_backgrounds_dir, name)
        if path is None:
            raise HTTPException(status_code=404, detail="Background not found.")
        return FileResponse(path, media_type="image/gif" if path.suffix.lower() == ".gif" else "video/mp4")

    if _web_dir.is_dir():
        app.mount("/app", StaticFiles(directory=_web_dir), name="web")

    return app


_app: FastAPI | None = None


def get_app() -> FastAPI:
    global _app
    if _app is None:
        _app = create_app()
    return _app


def __getattr__(name: str) -> FastAPI:
    # Lazy: importing app.main must not build a second runtime when embedded.
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
