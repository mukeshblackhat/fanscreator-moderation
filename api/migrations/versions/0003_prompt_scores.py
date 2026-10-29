"""Prompt engine: scores stored with generated media, and an audit of every prompt decision.

* media.negative_prompt, media.prompt_scores (per-category 0..1), media.prompt_flags
  ("review:prompt_<category>" reasons that hold the output for a human)
* prompt_events: one row per prompt decision (generation or check). Stores a SHA-256
  of the prompt and its length, never the text.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
    ALTER TABLE media ADD COLUMN negative_prompt text;
    ALTER TABLE media ADD COLUMN prompt_scores jsonb;
    ALTER TABLE media ADD COLUMN prompt_flags jsonb NOT NULL DEFAULT '[]'::jsonb;

    CREATE TABLE prompt_events (
        id             bigserial PRIMARY KEY,
        workspace_id   text NOT NULL REFERENCES workspaces(id),
        user_id        text NOT NULL,
        media_id       uuid REFERENCES media(id),
        kind           text NOT NULL CHECK (kind IN ('generation', 'check')),
        decision       text NOT NULL CHECK (decision IN ('allow', 'review', 'refuse')),
        scores         jsonb NOT NULL,
        reasons        jsonb NOT NULL DEFAULT '[]'::jsonb,
        prompt_sha256  text NOT NULL,
        prompt_chars   integer NOT NULL,
        has_negative   boolean NOT NULL DEFAULT false,
        created_at     timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX prompt_events_ws_idx ON prompt_events (workspace_id, created_at DESC);
    CREATE INDEX prompt_events_user_idx ON prompt_events (user_id, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS prompt_events;
    ALTER TABLE media DROP COLUMN IF EXISTS prompt_flags;
    ALTER TABLE media DROP COLUMN IF EXISTS prompt_scores;
    ALTER TABLE media DROP COLUMN IF EXISTS negative_prompt;
    """)
