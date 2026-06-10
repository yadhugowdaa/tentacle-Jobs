"""FastAPI app: JSON read API for the tracker + serves the dark dashboard.

Run it with `uv run python -m tentacle_apply.cli serve` (or `uvicorn tentacle_apply.api:app`).
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import select

from tentacle_apply import tracker
from tentacle_apply.db.models import User
from tentacle_apply.db.session import get_session, init_db
from tentacle_apply.log import get_logger

log = get_logger(__name__)
_STATIC = Path(__file__).resolve().parent / "web_static"

app = FastAPI(title="tentacle-apply", version="0.1.0")


class _RunManager:
    """Tracks one in-process background run per user + a cooperative stop flag.

    Single-process only (matches the SQLite phase). Phase E replaces this with a real task queue.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}
        self._stop: set[int] = set()

    def is_running(self, user_id: int) -> bool:
        with self._lock:
            t = self._threads.get(user_id)
            return bool(t and t.is_alive())

    def request_stop(self, user_id: int) -> None:
        with self._lock:
            self._stop.add(user_id)

    def should_stop(self, user_id: int) -> bool:
        with self._lock:
            return user_id in self._stop

    def start(self, user_id: int, email: str, target: int, mode: str) -> bool:
        with self._lock:
            existing = self._threads.get(user_id)
            if existing and existing.is_alive():
                return False
            self._stop.discard(user_id)

            def _worker() -> None:
                from tentacle_apply.orchestrator import run_apply_loop

                try:
                    run_apply_loop(
                        user_email=email,
                        target=target,
                        mode=mode,
                        should_stop=lambda: RUNS.should_stop(user_id),
                    )
                except Exception as exc:  # noqa: BLE001 - background worker must not crash the server
                    log.exception("background run failed for %s: %s", email, str(exc)[:160])

            t = threading.Thread(target=_worker, name=f"run-user-{user_id}", daemon=True)
            self._threads[user_id] = t
            t.start()
            return True


RUNS = _RunManager()


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "tentacle-apply"}


@app.get("/api/users")
def users() -> list[dict[str, str]]:
    with get_session() as session:
        return [{"email": u.email} for u in session.exec(select(User)).all()]


@app.get("/api/stats")
def stats(email: str | None = None) -> JSONResponse:
    with get_session() as session:
        user = tracker.resolve_user(session, email)
        if user is None:
            return JSONResponse({"error": "no user"}, status_code=404)
        return JSONResponse(tracker.stats(session, user))


@app.get("/api/applications")
def applications(email: str | None = None) -> JSONResponse:
    with get_session() as session:
        user = tracker.resolve_user(session, email)
        if user is None:
            return JSONResponse({"error": "no user", "applications": []}, status_code=404)
        return JSONResponse({"applications": tracker.list_applications(session, user)})


@app.post("/api/runs")
def start_run(
    email: str | None = None, target: int | None = None, mode: str | None = None
) -> JSONResponse:
    from tentacle_apply.config import settings

    if mode is not None and mode not in ("prepare", "submit", "hitl"):
        return JSONResponse({"error": "mode must be prepare|submit|hitl"}, status_code=400)
    with get_session() as session:
        user = tracker.resolve_user(session, email)
        if user is None:
            return JSONResponse({"error": "no user"}, status_code=404)
        user_id, user_email = user.id, user.email
    started = RUNS.start(
        user_id,
        user_email,
        target if target is not None else settings.default_target_applications,
        mode or settings.run_mode,
    )
    if not started:
        return JSONResponse({"status": "already_running"}, status_code=409)
    return JSONResponse({"status": "started"})


@app.post("/api/runs/stop")
def stop_run(email: str | None = None) -> JSONResponse:
    with get_session() as session:
        user = tracker.resolve_user(session, email)
        if user is None:
            return JSONResponse({"error": "no user"}, status_code=404)
        RUNS.request_stop(user.id)
        return JSONResponse({"status": "stop_requested", "running": RUNS.is_running(user.id)})


@app.get("/api/runs")
def run_status(email: str | None = None) -> JSONResponse:
    with get_session() as session:
        user = tracker.resolve_user(session, email)
        if user is None:
            return JSONResponse({"error": "no user"}, status_code=404)
        return JSONResponse({"running": RUNS.is_running(user.id), **tracker.latest_run(session, user)})


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(_STATIC / "index.html")
