"""SQLite persistence (SQLAlchemy 2.x)."""
from __future__ import annotations

import secrets
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import config


class Base(DeclarativeBase):
    pass


def new_id(prefix: str = "") -> str:
    return prefix + secrets.token_hex(6)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("p_"))
    name: Mapped[str] = mapped_column(String(200))
    brief: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    kind: Mapped[str] = mapped_column(String(16), default="user")  # user | demo
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("a_"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # svg png jpeg webp pdf mp3 wav
    size: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(512))  # relative to the project dir
    thumb: Mapped[str | None] = mapped_column(String(512), nullable=True)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("r_"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="agent")  # agent | demo
    status: Mapped[str] = mapped_column(String(24), default="idle")
    model: Mapped[str] = mapped_column(String(200), default="")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    el_chars: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    outputs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Message(Base):
    """Conversation history in OpenAI chat format (one row per message)."""

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict] = mapped_column(JSON)  # the full message dict
    archived: Mapped[bool] = mapped_column(Integer, default=0)  # replaced by a summary
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    type: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON)
    ts: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("j_"))
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    command: Mapped[str] = mapped_column(Text)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued running done failed killed lost
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_path: Mapped[str] = mapped_column(String(512))
    progress: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)


_engine = None
_Session: sessionmaker | None = None
write_lock = threading.RLock()


def init_db(url: str | None = None) -> None:
    global _engine, _Session
    config.ensure_dirs()
    import os

    dbf = config.data_dir / "studio.db"
    for f in (dbf, dbf.with_name("studio.db-wal"), dbf.with_name("studio.db-shm")):
        if not f.exists():  # create 0600 up front: SQLite copies the mode to the WAL/SHM files
            os.close(os.open(f, os.O_WRONLY | os.O_CREAT, 0o600))
        else:
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
    _engine = create_engine(url or config.db_url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(_engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(_engine)

    _Session = sessionmaker(_engine, expire_on_commit=False)


@contextmanager
def session() -> Iterator[Session]:
    assert _Session is not None, "init_db() not called"
    with write_lock:
        s = _Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


def get_setting(key: str, default=None):
    with session() as s:
        row = s.get(Setting, key)
        return default if row is None else row.value


def set_setting(key: str, value) -> None:
    with session() as s:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value


def all_settings() -> dict:
    with session() as s:
        return {r.key: r.value for r in s.scalars(select(Setting))}


def to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
