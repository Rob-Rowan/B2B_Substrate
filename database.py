"""SQLAlchemy engine/session layer for B2B Substrate.

This module owns the single SQLAlchemy ``Engine`` and session factory
used across the application.  It intentionally never calls
``Base.metadata.create_all()`` in a destructive way: the existing
``leads`` and ``lead_touches`` tables in ``leads.db`` are treated as
pre-existing, immutable schema.  ``create_all()`` is only ever invoked
with ``checkfirst=True`` (the SQLAlchemy default), which is a strict
no-op for tables that already exist — it never issues ``DROP TABLE``,
``ALTER TABLE``, or any statement that could modify existing rows or
columns.  This behavior is exercised only so a fresh developer
checkout without ``leads.db`` still has a working schema; against the
production database it performs zero DDL.

The module exposes:

* :func:`get_engine` — the process-wide SQLAlchemy ``Engine``.
* :func:`get_session` — a context-managed ``Session`` factory.
* :func:`init_db` — an idempotent, additive-only schema guard.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_PATH
from models import Base

# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first use.

    The engine targets the existing ``leads.db`` SQLite file resolved
    from :data:`config.DATABASE_PATH`.  ``check_same_thread=False`` is
    set so the same engine can be shared across Streamlit's
    re-execution model, and WAL journal mode is enabled for safe
    concurrent read/write access — mirroring the previous raw
    ``sqlite3`` connection settings exactly.

    Returns:
        Engine: The shared SQLAlchemy engine instance.
    """
    global _engine
    if _engine is not None:
        return _engine

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        f"sqlite:///{DATABASE_PATH}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        """Apply per-connection PRAGMAs matching the legacy sqlite3 layer.

        Args:
            dbapi_connection: The raw DBAPI connection being configured.
            _connection_record: Unused SQLAlchemy pool connection record.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first use.

    Returns:
        sessionmaker[Session]: The shared SQLAlchemy session factory.
    """
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _SessionFactory


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a transactional SQLAlchemy session as a context manager.

    On successful exit the transaction is committed; on any exception
    it is rolled back and the exception is re-raised.  The session is
    always closed on exit.

    Yields:
        Session: An active SQLAlchemy session bound to the shared
            engine.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Ensure the ORM schema exists without altering existing tables.

    This calls ``Base.metadata.create_all(engine, checkfirst=True)``,
    which SQLAlchemy documents as a strict "create if absent" — for
    every table that already exists (as ``leads`` and ``lead_touches``
    do in the production database) this is a complete no-op: no
    ``ALTER TABLE``, no ``DROP TABLE``, and no data is touched.  It
    exists only so a brand-new environment without ``leads.db`` still
    boots with a valid schema.
    """
    Base.metadata.create_all(get_engine(), checkfirst=True)
