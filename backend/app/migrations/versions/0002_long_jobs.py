"""Long-horizon work: plans (todos + revisions), memory, checkpoints, artifacts, user
requests (questions/approvals/options), notifications; sub-agent + cost columns on runs.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("runs") as b:
        b.add_column(sa.Column("parent_run_id", sa.String(32), nullable=True))
        b.add_column(sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"))
        b.add_column(sa.Column("limits", sa.JSON, nullable=True))
        b.create_index("ix_runs_parent_run_id", ["parent_run_id"])
    op.create_table("todos", sa.Column("id", sa.String(32), primary_key=True), sa.Column("run_id", sa.String(32), nullable=False),
                    sa.Column("project_id", sa.String(32), nullable=False), sa.Column("parent_id", sa.String(32), nullable=True),
                    sa.Column("title", sa.String(300), nullable=False), sa.Column("detail", sa.Text, nullable=False),
                    sa.Column("status", sa.String(16), nullable=False), sa.Column("priority", sa.String(8), nullable=False),
                    sa.Column("order", sa.Integer, nullable=False), sa.Column("acceptance_criteria", sa.Text, nullable=False),
                    sa.Column("evidence", sa.JSON, nullable=True), sa.Column("note", sa.Text, nullable=True),
                    sa.Column("started_at", sa.Float, nullable=True), sa.Column("completed_at", sa.Float, nullable=True),
                    sa.Column("created_at", sa.Float, nullable=False), sa.Column("updated_at", sa.Float, nullable=False))
    for c in ("run_id", "project_id", "parent_id"):
        op.create_index(f"ix_todos_{c}", "todos", [c])
    op.create_table("plan_revisions", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                    sa.Column("run_id", sa.String(32), nullable=False), sa.Column("revision", sa.Integer, nullable=False),
                    sa.Column("reason", sa.Text, nullable=False), sa.Column("author", sa.String(16), nullable=False),
                    sa.Column("snapshot", sa.JSON, nullable=False), sa.Column("created_at", sa.Float, nullable=False))
    op.create_index("ix_plan_revisions_run_id", "plan_revisions", ["run_id"])
    op.create_table("memories", sa.Column("id", sa.String(32), primary_key=True), sa.Column("project_id", sa.String(32), nullable=False),
                    sa.Column("key", sa.String(200), nullable=False), sa.Column("value", sa.Text, nullable=False),
                    sa.Column("source", sa.String(16), nullable=False), sa.Column("created_at", sa.Float, nullable=False),
                    sa.Column("updated_at", sa.Float, nullable=False))
    op.create_index("ix_memories_project_id", "memories", ["project_id"])
    op.create_table("checkpoints", sa.Column("id", sa.String(32), primary_key=True), sa.Column("project_id", sa.String(32), nullable=False),
                    sa.Column("run_id", sa.String(32), nullable=True), sa.Column("label", sa.String(300), nullable=False),
                    sa.Column("commit", sa.String(64), nullable=False), sa.Column("auto", sa.Integer, nullable=False),
                    sa.Column("meta", sa.JSON, nullable=True), sa.Column("created_at", sa.Float, nullable=False))
    op.create_index("ix_checkpoints_project_id", "checkpoints", ["project_id"])
    op.create_table("artifacts", sa.Column("id", sa.String(32), primary_key=True), sa.Column("project_id", sa.String(32), nullable=False),
                    sa.Column("run_id", sa.String(32), nullable=True), sa.Column("type", sa.String(24), nullable=False),
                    sa.Column("path", sa.String(512), nullable=True), sa.Column("title", sa.String(300), nullable=False),
                    sa.Column("version_group", sa.String(200), nullable=False), sa.Column("version", sa.Integer, nullable=False),
                    sa.Column("metadata", sa.JSON, nullable=True), sa.Column("favorite", sa.Integer, nullable=False),
                    sa.Column("created_at", sa.Float, nullable=False))
    for c in ("project_id", "run_id", "version_group"):
        op.create_index(f"ix_artifacts_{c}", "artifacts", [c])
    op.create_table("user_requests", sa.Column("id", sa.String(32), primary_key=True), sa.Column("run_id", sa.String(32), nullable=False),
                    sa.Column("kind", sa.String(16), nullable=False), sa.Column("payload", sa.JSON, nullable=False),
                    sa.Column("status", sa.String(16), nullable=False), sa.Column("answer", sa.JSON, nullable=True),
                    sa.Column("tool_call_id", sa.String(64), nullable=True), sa.Column("created_at", sa.Float, nullable=False),
                    sa.Column("answered_at", sa.Float, nullable=True))
    op.create_index("ix_user_requests_run_id", "user_requests", ["run_id"])
    op.create_table("notifications", sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                    sa.Column("project_id", sa.String(32), nullable=False), sa.Column("run_id", sa.String(32), nullable=True),
                    sa.Column("level", sa.String(16), nullable=False), sa.Column("message", sa.Text, nullable=False),
                    sa.Column("read", sa.Integer, nullable=False), sa.Column("created_at", sa.Float, nullable=False))
    op.create_index("ix_notifications_project_id", "notifications", ["project_id"])


def downgrade():
    for t in ("notifications", "user_requests", "artifacts", "checkpoints", "memories", "plan_revisions", "todos"):
        op.drop_table(t)
    with op.batch_alter_table("runs") as b:
        b.drop_index("ix_runs_parent_run_id")
        b.drop_column("limits")
        b.drop_column("cost_usd")
        b.drop_column("parent_run_id")
