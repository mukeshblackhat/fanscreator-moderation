"""Initial schema: workspaces, media, jobs, provider ledger, scores, decisions.

The safety guarantees live here, in the database, not only in Python:

* media.status can only be one of four values.
* media.status = 'published' is refused unless media.decision_id points to a
  'published' decision for the same media that covered every frame.
* jobs: at most one non-terminal job per media (partial unique index).
* scores: primary key on media_id, so "scored successfully at most once" is a fact.
* callback_receipts: primary key on provider_job_id, so a duplicate callback is a no-op.

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE TABLE workspaces (
    id          text PRIMARY KEY,
    verified    boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id          text PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
    user_id       text NOT NULL REFERENCES users(id),
    workspace_id  text NOT NULL REFERENCES workspaces(id),
    role          text NOT NULL CHECK (role IN ('admin', 'member')),
    PRIMARY KEY (user_id, workspace_id)
);

CREATE TABLE media (
    id                uuid PRIMARY KEY,
    workspace_id      text NOT NULL REFERENCES workspaces(id),
    type              text NOT NULL CHECK (type IN ('image', 'video')),
    source            text NOT NULL CHECK (source IN ('upload', 'generated')),
    prompt            text,
    frame_count       integer NOT NULL CHECK (frame_count >= 1),
    frames            jsonb NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'published', 'held', 'blocked')),
    is_explicit       boolean NOT NULL DEFAULT false,
    reasons           jsonb NOT NULL DEFAULT '[]'::jsonb,
    held_reason_kind  text CHECK (held_reason_kind IN ('score', 'screening_unavailable', 'provider_malformed')),
    rescreen_pending  boolean NOT NULL DEFAULT false,
    screening_token   uuid,
    decision_id       uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (type <> 'image' OR frame_count = 1),
    CHECK (source <> 'generated' OR prompt IS NOT NULL),
    CHECK ((status = 'held') = (held_reason_kind IS NOT NULL))
);
CREATE INDEX media_workspace_status_idx ON media (workspace_id, status, created_at);
CREATE INDEX media_rescreen_idx ON media (created_at) WHERE rescreen_pending;

CREATE TABLE jobs (
    id              bigserial PRIMARY KEY,
    media_id        uuid NOT NULL REFERENCES media(id),
    workspace_id    text NOT NULL REFERENCES workspaces(id),
    lane            text NOT NULL CHECK (lane IN ('image', 'video', 'rescreen')),
    state           text NOT NULL DEFAULT 'queued'
                    CHECK (state IN ('queued', 'running', 'waiting_callback', 'done', 'dead')),
    run_at          timestamptz NOT NULL DEFAULT now(),
    attempts        integer NOT NULL DEFAULT 0,
    attempt_budget  integer NOT NULL DEFAULT 3,
    rate_limited_attempts integer NOT NULL DEFAULT 0,
    lease_until     timestamptz,
    lease_token     uuid,
    provider_job_id text,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
-- At most one active job per media: a redelivery cannot create a second one.
CREATE UNIQUE INDEX jobs_one_active_per_media
    ON jobs (media_id) WHERE state IN ('queued', 'running', 'waiting_callback');
CREATE INDEX jobs_claim_idx ON jobs (lane, state, run_at);
CREATE INDEX jobs_workspace_running_idx ON jobs (lane, workspace_id) WHERE state = 'running';

-- Our own ledger of money spent. Written BEFORE the HTTP call goes out.
CREATE TABLE provider_calls (
    id               uuid PRIMARY KEY,
    media_id         uuid NOT NULL REFERENCES media(id),
    job_id           bigint REFERENCES jobs(id),
    attempt          integer NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('sync', 'async_submit')),
    provider_job_id  text,
    outcome          text NOT NULL DEFAULT 'in_flight'
                     CHECK (outcome IN ('in_flight', 'ok', 'accepted', 'timeout', 'rate_limited',
                                        'server_error', 'malformed', 'abandoned')),
    http_status      integer,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz
);
CREATE INDEX provider_calls_media_idx ON provider_calls (media_id);

-- Primary key on media_id: a media item can be scored successfully at most once.
CREATE TABLE scores (
    media_id          uuid PRIMARY KEY REFERENCES media(id),
    provider_call_id  uuid REFERENCES provider_calls(id),
    frames            jsonb NOT NULL,
    received_at       timestamptz NOT NULL DEFAULT now()
);

-- Decisions are append-only history; media.decision_id points at the current one.
CREATE TABLE decisions (
    id             uuid PRIMARY KEY,
    media_id       uuid NOT NULL REFERENCES media(id),
    status         text NOT NULL CHECK (status IN ('published', 'held', 'blocked')),
    reasons        jsonb NOT NULL DEFAULT '[]'::jsonb,
    frames_scored  integer NOT NULL,
    frame_count    integer NOT NULL,
    policy_version text NOT NULL DEFAULT 'v1',
    decided_by     text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decisions_media_idx ON decisions (media_id);
ALTER TABLE media ADD CONSTRAINT media_decision_fk
    FOREIGN KEY (decision_id) REFERENCES decisions(id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE review_actions (
    id          bigserial PRIMARY KEY,
    media_id    uuid NOT NULL REFERENCES media(id),
    user_id     text NOT NULL REFERENCES users(id),
    action      text NOT NULL CHECK (action IN ('approve', 'reject')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE callback_receipts (
    provider_job_id  text PRIMARY KEY,
    media_id         uuid NOT NULL REFERENCES media(id),
    body_sha256      text NOT NULL,
    received_at      timestamptz NOT NULL DEFAULT now()
);

-- The one door to 'published': refuse it unless a complete, matching decision exists.
CREATE FUNCTION media_require_decision_for_publish() RETURNS trigger AS $$
DECLARE
    d decisions%ROWTYPE;
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
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER media_publish_guard
    BEFORE INSERT OR UPDATE ON media
    FOR EACH ROW EXECUTE FUNCTION media_require_decision_for_publish();
"""

DOWNGRADE_SQL = r"""
DROP TRIGGER IF EXISTS media_publish_guard ON media;
DROP FUNCTION IF EXISTS media_require_decision_for_publish();
DROP TABLE IF EXISTS callback_receipts, review_actions, scores, provider_calls, jobs CASCADE;
ALTER TABLE IF EXISTS media DROP CONSTRAINT IF EXISTS media_decision_fk;
DROP TABLE IF EXISTS decisions, media, memberships, users, workspaces CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
