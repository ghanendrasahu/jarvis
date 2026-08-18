from __future__ import annotations

import socket
import threading

import uvicorn

from app.bootstrap import Runtime
from app.core.events import EventHub
from app.main import create_app


def _pick_free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


class EmbeddedWebServer:
    """Runs the JARVIS web API in a daemon thread inside the desktop HUD process.

    The app is bound to the caller's own Runtime + EventHub so the embedded web
    deck drives the exact same orchestrator/scheduler as the Tkinter HUD.
    """

    def __init__(self, runtime: Runtime, hub: EventHub, port: int) -> None:
        self._runtime = runtime
        self._hub = hub
        self._port = port
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/"

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._port = _pick_free_port(self._port)
        app = create_app(runtime=self._runtime, hub=self._hub, manage_lifecycle=False)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._server = server
        self._thread = threading.Thread(target=server.run, daemon=True, name="jarvis-webview-server")
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._server = None
