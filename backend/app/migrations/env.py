"""Alembic environment: migrations run in-process on the app's connection (see db.migrate)."""
from alembic import context

conn = context.config.attributes["connection"]
context.configure(connection=conn, target_metadata=None, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
