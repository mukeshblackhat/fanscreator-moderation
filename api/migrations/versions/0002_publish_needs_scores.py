"""Publishing needs scores for every frame, not just a decision that says so.

v0 wrote decision rows claiming full coverage after scoring 8 frames (or none,
on a timeout). The guard from 0001 trusted that row. Now `published` also
requires a `scores` row with exactly frame_count frames, and the decision's
frames_scored must match what was actually stored.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION media_require_decision_for_publish() RETURNS trigger AS $$
DECLARE
    d decisions%ROWTYPE;
    stored integer;
BEGIN
    IF NEW.status = 'published' THEN
        IF NEW.decision_id IS NULL THEN
            RAISE EXCEPTION 'media % cannot be published without a decision', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT * INTO d FROM decisions WHERE id = NEW.decision_id;
        IF NOT FOUND
           OR d.media_id <> NEW.id
           OR d.status <> 'published'
           OR d.frame_count <> NEW.frame_count
           OR d.frames_scored <> NEW.frame_count THEN
            RAISE EXCEPTION 'media % cannot be published: decision % is missing or incomplete',
                NEW.id, NEW.decision_id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT jsonb_array_length(frames) INTO stored FROM scores WHERE media_id = NEW.id;
        IF stored IS NULL OR stored <> NEW.frame_count THEN
            RAISE EXCEPTION 'media % cannot be published: provider scores cover % of % frames',
                NEW.id, coalesce(stored, 0), NEW.frame_count
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE INDEX IF NOT EXISTS provider_calls_job_idx ON provider_calls (job_id, outcome);
CREATE INDEX IF NOT EXISTS provider_calls_provider_job_idx ON provider_calls (provider_job_id)
    WHERE provider_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS jobs_waiting_callback_idx ON jobs (updated_at) WHERE state = 'waiting_callback';
"""

DOWNGRADE_SQL = r"""
DROP INDEX IF EXISTS jobs_waiting_callback_idx;
DROP INDEX IF EXISTS provider_calls_provider_job_idx;
DROP INDEX IF EXISTS provider_calls_job_idx;
CREATE OR REPLACE FUNCTION media_require_decision_for_publish() RETURNS trigger AS $$
DECLARE
    d decisions%ROWTYPE;
BEGIN
    IF NEW.status = 'published' THEN
        IF NEW.decision_id IS NULL THEN
            RAISE EXCEPTION 'media % cannot be published without a decision', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT * INTO d FROM decisions WHERE id = NEW.decision_id;
        IF NOT FOUND OR d.media_id <> NEW.id OR d.status <> 'published'
           OR d.frame_count <> NEW.frame_count OR d.frames_scored <> NEW.frame_count THEN
            RAISE EXCEPTION 'media % cannot be published: decision % is missing or incomplete',
                NEW.id, NEW.decision_id USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
