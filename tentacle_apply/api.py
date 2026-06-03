"""FastAPI app: JSON read API for the tracker + serves the dark dashboard.

Run it with `uv run python -m tentacle_apply.cli serve` (or `uvicorn tentacle_apply.api:app`).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import select

from tentacle_apply import tracker
from tentacle_apply.db.models import User
from tentacle_apply.db.session import get_session, init_db

_STATIC = Path(__file__).resolve().parent / "web_static"

app = FastAPI(title="tentacle-apply", version="0.1.0")


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


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(_STATIC / "index.html")
