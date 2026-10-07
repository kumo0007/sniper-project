from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def reset_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, future=True)
        _Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
    return _engine


def _sessionmaker() -> sessionmaker[Session]:
    get_engine()
    assert _Session is not None
    return _Session


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    module = type(dbapi_connection).__module__
    if "sqlite" not in module:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _migrate_sqlite(engine: Engine) -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(accounts)")).fetchall()}
        if "auth_method" not in columns:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN auth_method VARCHAR(32) DEFAULT 'device_code'"))
        if "credential_hint" not in columns:
            conn.execute(text("ALTER TABLE accounts ADD COLUMN credential_hint VARCHAR(64)"))


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_sqlite(engine)
    from app.services.settings_store import seed_settings

    with session_scope() as db:
        seed_settings(db)


@contextmanager
def session_scope():
    db = _sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
