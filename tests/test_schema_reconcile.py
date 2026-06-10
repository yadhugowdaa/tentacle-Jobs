"""Regression guard: init_db must heal column drift on existing SQLite tables.

`create_all` only creates *missing tables*, never new columns. When a model gains a field, an old DB
that predates it would break (e.g. `no such column: runs.mode`). `reconcile_sqlite_columns` closes
that gap by adding any missing columns via `ALTER TABLE ADD COLUMN`.
"""

from sqlalchemy import create_engine, text

from tentacle_apply.db.session import reconcile_sqlite_columns


def test_reconcile_adds_missing_columns(tmp_path):
    db = tmp_path / "drift.db"
    engine = create_engine(f"sqlite:///{db}")

    # Simulate an *old* runs table created before `mode`/`status` existed.
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE runs (id INTEGER PRIMARY KEY, user_id INTEGER, target_count INTEGER, applied_count INTEGER)"
        )
        conn.exec_driver_sql("INSERT INTO runs (id, user_id) VALUES (1, 7)")

    reconcile_sqlite_columns(engine)

    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql('PRAGMA table_info("runs")').fetchall()}
        # New columns from the Run model are now present.
        assert {"mode", "status"} <= cols
        # The scalar default was applied (mode defaults to "prepare"); the pre-existing row is intact.
        row = conn.execute(text("SELECT user_id, mode FROM runs WHERE id = 1")).first()
        assert row[0] == 7
        assert row[1] == "prepare"


def test_reconcile_is_idempotent(tmp_path):
    db = tmp_path / "ok.db"
    engine = create_engine(f"sqlite:///{db}")
    from sqlmodel import SQLModel

    import tentacle_apply.db.models  # noqa: F401 - register tables

    SQLModel.metadata.create_all(engine)
    # Running again on an already-correct schema must be a no-op (no crash, no dupes).
    reconcile_sqlite_columns(engine)
    reconcile_sqlite_columns(engine)
