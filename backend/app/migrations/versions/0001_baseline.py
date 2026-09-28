"""Baseline: the Luma Studio 1.0 schema (accounts, projects, runs, events, jobs).

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("settings", sa.Column("key", sa.String(64), primary_key=True), sa.Column("value", sa.JSON, nullable=True))
    op.create_table("users", sa.Column("id", sa.String(32), primary_key=True), sa.Column("username", sa.String(64), nullable=False),
                    sa.Column("password_hash", sa.String(256), nullable=False), sa.Column("created_at", sa.Float, nullable=False))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_table("user_sessions", sa.Column("token_hash", sa.String(64), primary_key=True),
                    sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("created_at", sa.Float, nullable=False), sa.Column("expires_at", sa.Float, nullable=False))
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_table("projects", sa.Column("id", sa.String(32), primary_key=True), sa.Column("owner_id", sa.String(32), nullable=True),
                    sa.Column("name", sa.String(200), nullable=False), sa.Column("brief", sa.Text, nullable=False),
                    sa.Column("settings", sa.JSON, nullable=False), sa.Column("kind", sa.String(16), nullable=False),
                    sa.Column("created_at", sa.Float, nullable=False), sa.Column("updated_at", sa.Float, nullable=False))
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_table("assets", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("filename", sa.String(255), nullable=False), sa.Column("kind", sa.String(16), nullable=False),
                    sa.Column("size", sa.Integer, nullable=False), sa.Column("path", sa.String(512), nullable=False),
                    sa.Column("thumb", sa.String(512), nullable=True), sa.Column("analysis", sa.JSON, nullable=True),
                    sa.Column("created_at", sa.Float, nullable=False))
    op.create_index("ix_assets_project_id", "assets", ["project_id"])
    op.create_table("runs", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("kind", sa.String(16), nullable=False), sa.Column("status", sa.String(24), nullable=False),
                    sa.Column("model", sa.String(200), nullable=False), sa.Column("steps", sa.Integer, nullable=False),
                    sa.Column("prompt_tokens", sa.Integer, nullable=False), sa.Column("completion_tokens", sa.Integer, nullable=False),
                    sa.Column("el_chars", sa.Integer, nullable=False), sa.Column("error", sa.Text, nullable=True),
                    sa.Column("summary", sa.Text, nullable=True), sa.Column("outputs", sa.JSON, nullable=True),
                    sa.Column("created_at", sa.Float, nullable=False), sa.Column("updated_at", sa.Float, nullable=False))
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_table("messages", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                    sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("role", sa.String(16), nullable=False), sa.Column("content", sa.JSON, nullable=False),
                    sa.Column("archived", sa.Integer, nullable=False), sa.Column("created_at", sa.Float, nullable=False))
    op.create_index("ix_messages_run_id", "messages", ["run_id"])
    op.create_table("events", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("run_id", sa.String(32), nullable=False),
                    sa.Column("type", sa.String(32), nullable=False), sa.Column("data", sa.JSON, nullable=False), sa.Column("ts", sa.Float, nullable=False))
    op.create_index("ix_events_run_id", "events", ["run_id"])
    op.create_table("jobs", sa.Column("id", sa.String(32), primary_key=True), sa.Column("project_id", sa.String(32), nullable=False),
                    sa.Column("run_id", sa.String(32), nullable=True), sa.Column("name", sa.String(120), nullable=False),
                    sa.Column("command", sa.Text, nullable=False), sa.Column("pid", sa.Integer, nullable=True),
                    sa.Column("status", sa.String(16), nullable=False), sa.Column("exit_code", sa.Integer, nullable=True),
                    sa.Column("log_path", sa.String(512), nullable=False), sa.Column("progress", sa.JSON, nullable=True),
                    sa.Column("tool_call_id", sa.String(64), nullable=True), sa.Column("started_at", sa.Float, nullable=True),
                    sa.Column("finished_at", sa.Float, nullable=True))
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_run_id", "jobs", ["run_id"])


def downgrade():
    for t in ("jobs", "events", "messages", "runs", "assets", "projects", "user_sessions", "users", "settings"):
        op.drop_table(t)
