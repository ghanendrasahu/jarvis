from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

Listener = Callable[[str, dict[str, Any]], None]


class EventHub:
    """Thread-safe pub/sub hub for live dashboard events. Additive only."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def publish(self, topic: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(topic, payload or {})
            except Exception:
                pass
