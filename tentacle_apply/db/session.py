"""Engine + session helpers. SQLite by default; set DATABASE_URL for Postgres."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from tentacle_apply.config import DATA_DIR, RESUMES_DIR, settings
from tentacle_apply.db import models  # noqa: F401 - registers tables on SQLModel.metadata
from tentacle_apply.log import get_logger

log = get_logger(__name__)

_is_sqlite = settings.db_url.startswith("sqlite")
_engine = create_engine(
    settings.db_url,
    echo=False,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)


def init_db() -> None:
    """Create data dirs + tables, then reconcile columns added to models since the DB was created.

    `create_all` only creates *missing tables* — it never adds new columns to an existing table. As
    models evolve, that silently drifts an old DB out of sync (e.g. a query for `runs.mode` blows up).
    For SQLite we close that gap with a dependency-free auto-migration: add any missing column via
    `ALTER TABLE ADD COLUMN`. (For Postgres, use a real migration tool.)
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(_engine)
    if _is_sqlite:
        reconcile_sqlite_columns(_engine)


def _sql_literal(value: object) -> str | None:
    """Render a scalar Python default as a SQLite literal, or None if it isn't a simple scalar."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def reconcile_sqlite_columns(engine) -> None:
    """Add any model columns missing from existing SQLite tables (additive, non-destructive)."""
    with engine.begin() as conn:
        for table_name, table in SQLModel.metadata.tables.items():
            try:
                rows = conn.exec_driver_sql(f'PRAGMA table_info("{table_name}")').fetchall()
            except Exception:  # noqa: BLE001 - table may not exist yet on a fresh/other dialect
                continue
            if not rows:
                continue
            existing = {r[1] for r in rows}
            for col in table.columns:
                if col.name in existing:
                    continue
                col_type = col.type.compile(dialect=engine.dialect)
                # Add as nullable (existing rows have no value); attach a scalar default when the model
                # defines one, so new behaviour matches the ORM and old rows get a sane fallback.
                default_sql = ""
                default = getattr(col, "default", None)
                if default is not None and getattr(default, "is_scalar", False):
                    lit = _sql_literal(default.arg)
                    if lit is not None:
                        default_sql = f" DEFAULT {lit}"
                ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}{default_sql}'
                try:
                    conn.exec_driver_sql(ddl)
                    log.info("schema: added missing column %s.%s", table_name, col.name)
                except Exception as exc:  # noqa: BLE001 - never let reconcile crash startup
                    log.warning("schema: could not add %s.%s: %s", table_name, col.name, str(exc)[:120])


def get_session() -> Session:
    # expire_on_commit=False so already-loaded rows stay usable after the session closes
    # (we read jobs, compute, then commit Match rows and still return the job data).
    return Session(_engine, expire_on_commit=False)
