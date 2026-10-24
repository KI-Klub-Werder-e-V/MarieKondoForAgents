-- c03-joy-test-scorecard: rubric, evidence, criterion scores, verdicts, reviews, category maturity.
-- Shared DDL for Postgres 16 and DuckDB. Rollback: 003_c03_joy_test_scorecard.down.sql. Source data is never touched.

CREATE TABLE rubric_version (                -- immutable after publish (catalog guard + Postgres trigger)
    version      TEXT PRIMARY KEY,
    content      TEXT NOT NULL,              -- JSON rubric
    content_hash TEXT NOT NULL,
    published_at TIMESTAMP NOT NULL,
    published_by TEXT NOT NULL
);

CREATE TABLE scorecard_run (
    run_id          TEXT PRIMARY KEY,
    charter_id      TEXT NOT NULL,
    charter_version INTEGER NOT NULL,
    rubric_version  TEXT NOT NULL,
    config          TEXT NOT NULL,           -- JSON thresholds used by this run
    run_trigger     TEXT NOT NULL,           -- manual | inventory_change | fix_completed | freshness_alert
    actor           TEXT NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    summary         TEXT                     -- JSON {scored, out_of_scope, blocked}
);

CREATE TABLE asset_evidence (                -- facts fed by probes' sources and later changes (04, 06, 07, 09)
    evidence_id  TEXT PRIMARY KEY,
    asset_uri    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    payload      TEXT NOT NULL,              -- JSON: references, hashes, counts and flags only
    evidence_ref TEXT NOT NULL,
    captured_at  TIMESTAMP NOT NULL,
    recorded_at  TIMESTAMP NOT NULL,
    recorded_by  TEXT NOT NULL
);

CREATE TABLE criterion_score (
    run_id         TEXT NOT NULL,
    asset_uri      TEXT NOT NULL,
    criterion      TEXT NOT NULL,
    level          INTEGER,                  -- 0-3, NULL = unknown
    probe          TEXT NOT NULL,
    evidence_ref   TEXT,
    evidence_at    TIMESTAMP,
    evidence       TEXT NOT NULL,            -- JSON: references, hashes, counts only
    stale_evidence BOOLEAN NOT NULL,
    rubric_version TEXT NOT NULL,
    PRIMARY KEY (run_id, asset_uri, criterion)
);

CREATE TABLE verdict (
    verdict_id     TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    asset_uri      TEXT NOT NULL,
    charter_id     TEXT NOT NULL,
    category_id    TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    proposed       TEXT NOT NULL,            -- keep | fix | discard
    reason         TEXT,                     -- discard: irrelevant | redundant | stale_unowned
    reason_ref     TEXT,                     -- e.g. the winning asset
    fix_items      TEXT NOT NULL,            -- JSON [{criterion, level, capability}]
    has_unknown    BOOLEAN NOT NULL,
    inventory_hash TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL
);

CREATE TABLE verdict_review (                -- append-only history per asset
    review_id      TEXT PRIMARY KEY,
    asset_uri      TEXT NOT NULL,
    verdict_id     TEXT NOT NULL,
    action         TEXT NOT NULL,            -- propose | accept | override | reverse
    actor          TEXT NOT NULL,
    from_verdict   TEXT,
    to_verdict     TEXT NOT NULL,
    justification  TEXT,
    rubric_version TEXT NOT NULL,
    reviewed_at    TIMESTAMP NOT NULL
);

CREATE TABLE category_maturity (
    run_id      TEXT NOT NULL,
    category_id TEXT NOT NULL,
    aspect      TEXT NOT NULL,
    criteria    TEXT NOT NULL,               -- JSON {criterion: minimum level}
    assets      INTEGER NOT NULL,
    meeting     INTEGER NOT NULL,
    share       DOUBLE PRECISION NOT NULL,
    state       TEXT NOT NULL,               -- poorly_prepared | in_progress | agent_ready
    PRIMARY KEY (run_id, category_id, aspect)
);
