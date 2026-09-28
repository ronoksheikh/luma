"""Self-extending toolbox: tools (index + stats), tool calls, skills, plugins, audit log and an
FTS5 index over tools / skills / plugins.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "toolbox_tools", sa.Column("id", sa.String(32), primary_key=True), sa.Column("name", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False), sa.Column("project_id", sa.String(32), nullable=True),
        sa.Column("version", sa.String(32), nullable=False), sa.Column("enabled", sa.Integer, nullable=False),
        sa.Column("test_status", sa.String(16), nullable=False), sa.Column("test_output", sa.Text, nullable=True),
        sa.Column("tested_hash", sa.String(64), nullable=True), sa.Column("files_hash", sa.String(64), nullable=True),
        sa.Column("scan", sa.JSON, nullable=True), sa.Column("stats", sa.JSON, nullable=False), sa.Column("path", sa.String(512), nullable=False),
        sa.Column("manifest", sa.JSON, nullable=True), sa.Column("author", sa.String(16), nullable=False),
        sa.Column("description", sa.Text, nullable=False), sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("deprecated", sa.JSON, nullable=True), sa.Column("created_from_run", sa.String(32), nullable=True),
        sa.Column("created_at", sa.Float, nullable=False), sa.Column("updated_at", sa.Float, nullable=False))
    op.create_index("ix_toolbox_tools_name", "toolbox_tools", ["name"])
    op.create_index("ix_toolbox_tools_project_id", "toolbox_tools", ["project_id"])
    op.create_table(
        "toolbox_calls", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("tool_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False), sa.Column("version", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=True), sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("tool_call_id", sa.String(64), nullable=True), sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("duration_s", sa.Float, nullable=False),
        sa.Column("error", sa.Text, nullable=True), sa.Column("created_at", sa.Float, nullable=False))
    for c in ("tool_id", "project_id", "run_id"):
        op.create_index(f"ix_toolbox_calls_{c}", "toolbox_calls", [c])
    op.create_table(
        "skills", sa.Column("id", sa.String(32), primary_key=True), sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=False), sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("tools_used", sa.JSON, nullable=False), sa.Column("author", sa.String(16), nullable=False),
        sa.Column("path", sa.String(512), nullable=False), sa.Column("created_from_run", sa.String(32), nullable=True),
        sa.Column("reads", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float, nullable=False), sa.Column("updated_at", sa.Float, nullable=False))
    op.create_index("ix_skills_name", "skills", ["name"], unique=True)
    op.create_table(
        "plugins", sa.Column("id", sa.String(32), primary_key=True), sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("version", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Integer, nullable=False), sa.Column("test_status", sa.String(16), nullable=False),
        sa.Column("test_output", sa.Text, nullable=True), sa.Column("description", sa.Text, nullable=False),
        sa.Column("author", sa.String(16), nullable=False), sa.Column("path", sa.String(512), nullable=False),
        sa.Column("meta", sa.JSON, nullable=True), sa.Column("created_from_run", sa.String(32), nullable=True),
        sa.Column("created_at", sa.Float, nullable=False), sa.Column("updated_at", sa.Float, nullable=False))
    op.create_index("ix_plugins_name", "plugins", ["name"], unique=True)
    op.create_table(
        "toolbox_audit", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("action", sa.String(32), nullable=False), sa.Column("target", sa.String(200), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=True), sa.Column("detail", sa.JSON, nullable=True),
        sa.Column("created_at", sa.Float, nullable=False))
    op.execute("CREATE VIRTUAL TABLE toolbox_fts USING fts5(kind UNINDEXED, ref UNINDEXED, scope UNINDEXED, project_id UNINDEXED, "
               "name, description, body, tags, tokenize='porter unicode61')")


def downgrade():
    op.execute("DROP TABLE IF EXISTS toolbox_fts")
    for t in ("toolbox_audit", "plugins", "skills", "toolbox_calls", "toolbox_tools"):
        op.drop_table(t)
