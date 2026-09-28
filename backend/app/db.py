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


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("u_"))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("p_"))
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
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
    parent_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # sub-agent runs
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    limits: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # sub-agent budget / allowed tools
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


class Todo(Base):
    __tablename__ = "todos"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("t_"))
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending in_progress blocked done skipped failed
    priority: Mapped[str] = mapped_column(String(8), default="medium")  # high medium low
    order: Mapped[int] = mapped_column(Integer, default=0)
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list | None] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class PlanRevision(Base):
    __tablename__ = "plan_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(16), default="agent")  # agent | user
    snapshot: Mapped[list] = mapped_column(JSON)  # the plan BEFORE this revision
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("m_"))
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="agent")  # agent | user
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("c_"))
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    label: Mapped[str] = mapped_column(String(300))
    commit: Mapped[str] = mapped_column(String(64))
    auto: Mapped[bool] = mapped_column(Integer, default=0)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("art_"))
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(24))  # video image audio file comparison storyboard timeline code table palette font grid
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    version_group: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    favorite: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class UserRequest(Base):
    """A pending question / approval / options pick (survives reloads and restarts)."""

    __tablename__ = "user_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("q_"))
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # ask | approval | options
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending answered timeout cancelled
    answer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    answered_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


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

    migrate(_engine)

    _Session = sessionmaker(_engine, expire_on_commit=False)


def migrate(engine) -> None:
    """Bring the schema to head with Alembic (``backend/app/migrations``).

    Databases created before Alembic was introduced have tables but no ``alembic_version``:
    they get the one pre-Alembic fix-up (``projects.owner_id``), are stamped at the
    baseline revision and then upgraded, so existing projects keep all their data."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    from pathlib import Path

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        cfg.attributes["connection"] = conn
        if "projects" in tables and "alembic_version" not in tables:
            cols = {c["name"] for c in inspect(conn).get_columns("projects")}
            if "owner_id" not in cols:
                conn.execute(text("ALTER TABLE projects ADD COLUMN owner_id VARCHAR(32)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_owner_id ON projects (owner_id)"))
            if "users" not in tables:  # pre-accounts databases
                Base.metadata.tables["users"].create(conn)
                Base.metadata.tables["user_sessions"].create(conn)
            command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")


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
    return {a.key: getattr(obj, a.key) for a in obj.__mapper__.column_attrs}
