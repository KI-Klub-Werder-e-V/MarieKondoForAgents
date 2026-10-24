-- c09-evaluate-and-monitor: golden question items and sets, evaluation runs and results, citation checks, judge
-- calibration, baselines; monitored assets, check results, incidents, drift results; next-category proposals.
-- Shared DDL for Postgres 16 and DuckDB. Rollback: 009_c09_evaluate_and_monitor.down.sql.
-- Decisions go to audit_event (step 'evaluate').

CREATE TABLE golden_item (                -- versioned; an edit is a new draft version, old versions stay unchanged
    item_id            TEXT NOT NULL,
    version            INTEGER NOT NULL,
    charter_id         TEXT NOT NULL,     -- the pilot
    question           TEXT NOT NULL,     -- user_log questions: masked before storage, never the raw text
    origin             TEXT NOT NULL,     -- user_log | domain_expert | synthetic
    category_id        TEXT NOT NULL,
    expected_behaviour TEXT NOT NULL,     -- answer | abstain | flag_conflict
    reference_answer   TEXT,
    expected_sources   TEXT NOT NULL,     -- JSON list of source or chunk IDs
    status             TEXT NOT NULL,     -- draft | approved
    approved_by        TEXT,
    approved_at        TIMESTAMP,
    retention_until    DATE,              -- user_log only: deleted unless re-approved
    content_hash       TEXT NOT NULL,
    created_by         TEXT NOT NULL,
    created_at         TIMESTAMP NOT NULL,
    PRIMARY KEY (item_id, version)
);

CREATE TABLE golden_set (
    charter_id   TEXT NOT NULL,
    version      INTEGER NOT NULL,
    items        TEXT NOT NULL,           -- JSON [[item_id, version], ...] pinned at activation
    status       TEXT NOT NULL,           -- active | retired
    minimums     TEXT NOT NULL,           -- JSON {min_items, min_negative, source: default|charter}
    activated_by TEXT NOT NULL,
    activated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (charter_id, version)
);

CREATE TABLE eval_run (
    run_id         TEXT PRIMARY KEY,
    charter_id     TEXT NOT NULL,
    set_version    INTEGER NOT NULL,
    agent_version  TEXT NOT NULL,
    principal      TEXT NOT NULL,         -- JSON {agent, user, groups}: the declared test principal
    judge_model    TEXT NOT NULL,
    judge_version  TEXT NOT NULL,
    judge_status   TEXT NOT NULL,         -- calibrated | uncalibrated
    status         TEXT NOT NULL,         -- running | completed | failed
    error          TEXT,
    excluded       TEXT NOT NULL,         -- JSON [{item_id, version, reason}]
    aggregates     TEXT,                  -- JSON {run: {...}, categories: {cat: {...}}}
    started_at     TIMESTAMP NOT NULL,
    ended_at       TIMESTAMP,
    started_by     TEXT NOT NULL
);

CREATE TABLE eval_result (
    run_id             TEXT NOT NULL,
    item_id            TEXT NOT NULL,
    item_version       INTEGER NOT NULL,
    category_id        TEXT NOT NULL,
    expected_behaviour TEXT NOT NULL,
    behaviour          TEXT,              -- the agent's declared behaviour
    answer             TEXT,
    retrieved          TEXT NOT NULL,     -- JSON list of chunk IDs
    cited              TEXT NOT NULL,     -- JSON list of cited source IDs
    latency_ms         INTEGER NOT NULL,
    faithfulness       DOUBLE PRECISION,
    context_precision  DOUBLE PRECISION,
    context_recall     DOUBLE PRECISION,
    citation_precision DOUBLE PRECISION,
    citation_recall    DOUBLE PRECISION,
    citation_ok        BOOLEAN NOT NULL,  -- deterministic check: exists, in scope, current version
    passed             BOOLEAN NOT NULL,
    reasons            TEXT NOT NULL,     -- JSON list, e.g. hallucinated_answer, stale_source
    details            TEXT NOT NULL,     -- JSON: unsupported claims, missing sources
    PRIMARY KEY (run_id, item_id)
);

CREATE TABLE citation_check (
    run_id    TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    source_id TEXT NOT NULL,
    status    TEXT NOT NULL,              -- ok | missing | out_of_scope | stale_source
    PRIMARY KEY (run_id, item_id, source_id)
);

CREATE TABLE judge_calibration (
    calibration_id TEXT PRIMARY KEY,
    charter_id     TEXT NOT NULL,
    judge_model    TEXT NOT NULL,
    judge_version  TEXT NOT NULL,
    items          INTEGER NOT NULL,
    kappa          DOUBLE PRECISION NOT NULL,
    confusion      TEXT NOT NULL,         -- JSON {human_label: {judge_label: count}}
    status         TEXT NOT NULL,         -- calibrated | uncalibrated
    created_by     TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL
);

CREATE TABLE baseline (                   -- history; the newest row is the active baseline
    charter_id   TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    run_id       TEXT NOT NULL,
    previous_run TEXT,
    kind         TEXT NOT NULL,           -- approve | revert
    approved_by  TEXT NOT NULL,
    approved_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (charter_id, seq)
);

CREATE TABLE category_focus (             -- next-category proposals; set only on human confirmation
    proposal_id  TEXT PRIMARY KEY,
    charter_id   TEXT NOT NULL,
    category_id  TEXT NOT NULL,
    run_id       TEXT,
    status       TEXT NOT NULL,           -- proposed | confirmed
    proposed_at  TIMESTAMP NOT NULL,
    confirmed_by TEXT,
    confirmed_at TIMESTAMP
);

CREATE TABLE monitored_asset (            -- from the c02 source inventory and the c08 data products
    asset_id          TEXT PRIMARY KEY,   -- source asset URI or product:<id>
    charter_id        TEXT NOT NULL,
    kind              TEXT NOT NULL,      -- source | product
    namespace         TEXT NOT NULL,      -- OpenLineage dataset namespace
    name              TEXT NOT NULL,      -- OpenLineage dataset name
    owner             TEXT,
    interval_days     DOUBLE PRECISION,
    warn_after_days   DOUBLE PRECISION,   -- owner override, else 1x interval
    error_after_days  DOUBLE PRECISION,   -- owner override, else 2x interval
    monitorable       BOOLEAN NOT NULL,
    reason            TEXT,               -- missing_owner | missing_update_interval
    freshness_status  TEXT,               -- ok | warn | error | NULL (not checked)
    last_change       TIMESTAMP,
    error_since       TIMESTAMP,          -- first run of the current freshness error streak
    updated_at        TIMESTAMP NOT NULL
);

CREATE TABLE check_result (
    result_id  TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    asset_id   TEXT NOT NULL,
    check_name TEXT NOT NULL,             -- freshness | volume | metadata_completeness | duplicate_share | schema_change
    status     TEXT NOT NULL,             -- ok | warn | error
    observed   TEXT,                      -- JSON value
    expected   TEXT,                      -- JSON range
    details    TEXT NOT NULL,             -- JSON: IDs and counts only, never record values
    origin     TEXT NOT NULL,             -- konmari | dbt | soda
    checked_at TIMESTAMP NOT NULL
);

CREATE TABLE incident (                   -- one open incident per asset and check
    incident_id   TEXT PRIMARY KEY,
    asset_id      TEXT NOT NULL,
    check_name    TEXT NOT NULL,
    severity      TEXT NOT NULL,          -- warn | error (highest seen)
    status        TEXT NOT NULL,          -- open | acknowledged | resolved | accepted_risk
    failure_count INTEGER NOT NULL,
    opened_at     TIMESTAMP NOT NULL,
    last_failed   TIMESTAMP NOT NULL,
    first_run     TEXT NOT NULL,
    notified_at   TIMESTAMP,
    escalated_at  TIMESTAMP,
    decided_by    TEXT,                   -- acknowledged / resolved / accepted by (or 'system')
    decided_at    TIMESTAMP,
    reason        TEXT,
    resolving_run TEXT,
    discard_proposals TEXT                -- JSON list of c04 proposal IDs
);

CREATE TABLE drift_result (
    result_id        TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    charter_id       TEXT NOT NULL,
    subject          TEXT NOT NULL,       -- questions | content
    reference_window TEXT NOT NULL,
    current_window   TEXT NOT NULL,
    method           TEXT NOT NULL,
    score            DOUBLE PRECISION NOT NULL,
    threshold        DOUBLE PRECISION NOT NULL,
    status           TEXT NOT NULL,       -- ok | warn
    details          TEXT NOT NULL,       -- JSON: topic shares, new topics
    checked_at       TIMESTAMP NOT NULL
);
