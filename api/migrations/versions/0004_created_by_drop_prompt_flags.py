"""Record who created each media item; drop the prompt review band.

* media.created_by: the X-User-Id that uploaded or generated it (audit).
* media.prompt_flags is dropped: prompts are now refused or allowed, never
  "generate but hold for review" (docs/decisions.md #36).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE media ADD COLUMN created_by text;
    ALTER TABLE media DROP COLUMN IF EXISTS prompt_flags;
    CREATE INDEX media_created_by_idx ON media (created_by, created_at);
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS media_created_by_idx;
    ALTER TABLE media ADD COLUMN prompt_flags jsonb NOT NULL DEFAULT '[]'::jsonb;
    ALTER TABLE media DROP COLUMN IF EXISTS created_by;
    """)
