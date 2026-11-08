"""
Database engine and session management.
"""

from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine

from app.config import DATABASE_URL

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Add it to .env, "
        "e.g. postgresql://user:password@localhost:5432/inbox_to_action "
        "— or, for the public demo (no external services needed), "
        "sqlite:///./demo.db with DEMO_MODE=true"
    )

_is_sqlite = DATABASE_URL.startswith("sqlite")

# SQLite's default driver forbids reusing a connection across threads; this
# app opens sessions from both request handlers and the APScheduler job, so
# SQLite needs check_same_thread=False. `timeout` (seconds) raises sqlite3's
# default 5s lock-wait before it gives up with "database is locked" — the
# demo's reset job, Gmail-poll job, and request handlers can all legitimately
# touch the same file around the same moment. Postgres needs neither.
_connect_args = {"check_same_thread": False, "timeout": 15} if _is_sqlite else {}

engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_wal(dbapi_connection, _):
        # WAL lets one writer and any number of readers proceed concurrently
        # instead of serializing on a single file lock — pairs with the
        # busy-timeout above to make concurrent demo traffic (reset job +
        # poll job + requests) fail a lot less often, not in theory but in
        # the specific multi-job way this app actually runs.
        dbapi_connection.execute("PRAGMA journal_mode=WAL")


def init_db() -> None:
    """Create tables that don't exist yet. No migration framework in v1."""
    import app.models  # noqa: F401 — ensure models are registered before create_all

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
