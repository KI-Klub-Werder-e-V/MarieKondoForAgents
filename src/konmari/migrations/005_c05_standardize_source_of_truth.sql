-- c05-standardize-source-of-truth: normalization specs and values, quarantine, entity resolution, source-of-truth
-- policy, golden records and the conflict register. Shared DDL for Postgres 16 and DuckDB. Raw values are only
-- ever copied here, never changed at the source. Decisions go to audit_event (step 'standardize').
-- Rollback: 005_c05_standardize_source_of_truth.down.sql

CREATE TABLE normalization_spec (         -- one version per change of the spec set (all normalize/*.yaml)
    version      INTEGER PRIMARY KEY,
    content      TEXT NOT NULL,           -- JSON {"fields": [...]}
    content_hash TEXT NOT NULL,
    author       TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL,
    diff         TEXT NOT NULL            -- JSON list of changed paths
);

CREATE TABLE normalize_run (
    run_id       TEXT PRIMARY KEY,        -- hash of spec version + input, so a re-run is a no-op
    spec_version INTEGER NOT NULL,
    input_hash   TEXT NOT NULL,
    sources      TEXT NOT NULL,           -- JSON list
    actor        TEXT NOT NULL,
    started_at   TIMESTAMP NOT NULL,
    metrics      TEXT NOT NULL            -- JSON per source field: read, canonical, quarantined, reasons, ...
);

CREATE TABLE normalized_value (           -- additive: raw next to canonical
    value_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    source       TEXT NOT NULL,
    record_id    TEXT NOT NULL,
    source_field TEXT NOT NULL,           -- source.object.column
    entity       TEXT NOT NULL,
    attribute    TEXT NOT NULL,
    raw          TEXT NOT NULL,
    canonical    TEXT NOT NULL,
    unit         TEXT,
    zone         TEXT,                    -- companion IANA zone or offset of datetimes
    match_key    TEXT,                    -- comparison only, never a display value
    flags        TEXT NOT NULL,           -- JSON list, e.g. ["syntax_valid"]
    normalizer   TEXT NOT NULL,
    spec_version INTEGER NOT NULL,
    origin       TEXT NOT NULL,           -- auto | manual
    processed_at TIMESTAMP NOT NULL,      -- processing time; event time is the canonical value itself
    withdrawn_at TIMESTAMP
);

CREATE TABLE quarantine (
    qid              TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    source           TEXT NOT NULL,
    record_id        TEXT NOT NULL,
    source_field     TEXT NOT NULL,
    entity           TEXT NOT NULL,
    attribute        TEXT NOT NULL,
    raw              TEXT NOT NULL,
    reason           TEXT NOT NULL,
    detail           TEXT,
    spec_version     INTEGER NOT NULL,
    owner            TEXT NOT NULL,
    status           TEXT NOT NULL,       -- open | resolved | unfixable | spec_change_requested
    resolution_value TEXT,
    resolved_by      TEXT,
    resolved_at      TIMESTAMP,
    created_at       TIMESTAMP NOT NULL
);

CREATE TABLE er_run (
    run_id          TEXT PRIMARY KEY,
    entity          TEXT NOT NULL,
    model_version   TEXT NOT NULL,        -- splink version + model config hash
    upper_threshold DOUBLE PRECISION NOT NULL,
    lower_threshold DOUBLE PRECISION NOT NULL,
    metrics         TEXT NOT NULL,        -- JSON: pairs, clusters, precision, recall, floors
    actor           TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    published_at    TIMESTAMP,
    published_by    TEXT,
    override_reason TEXT
);

CREATE TABLE er_pair (                    -- scored cross-source pairs at or above the lower threshold
    entity      TEXT NOT NULL,
    rec_a       TEXT NOT NULL,            -- source:record_id, rec_a < rec_b
    rec_b       TEXT NOT NULL,
    probability DOUBLE PRECISION NOT NULL,
    origin      TEXT NOT NULL,            -- auto (>= upper) | review (between the thresholds)
    label       TEXT,                     -- match | non_match (negative label, never auto-linked again)
    reviewer    TEXT,
    reviewed_at TIMESTAMP,
    split_id    TEXT,                     -- set when a cluster split cut this link
    prev_label  TEXT,
    run_id      TEXT NOT NULL,
    PRIMARY KEY (entity, rec_a, rec_b)
);

CREATE TABLE er_cluster (
    row_id     TEXT PRIMARY KEY,
    cluster_id TEXT NOT NULL,
    entity     TEXT NOT NULL,
    members    TEXT NOT NULL,             -- JSON sorted list of source:record_id
    run_id     TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    retired_at TIMESTAMP                  -- history: earlier cluster states stay
);

CREATE TABLE crosswalk (
    entity      TEXT NOT NULL,
    source      TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    cluster_id  TEXT NOT NULL,
    link_origin TEXT NOT NULL,            -- auto | review | dedup | single
    run_id      TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    active      BOOLEAN NOT NULL
);

CREATE TABLE policy_version (
    entity       TEXT NOT NULL,
    version      INTEGER NOT NULL,
    status       TEXT NOT NULL,           -- pending | approved
    active       BOOLEAN NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    author       TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL,
    approver     TEXT,
    approved_at  TIMESTAMP,
    diff         TEXT NOT NULL,
    impact       TEXT NOT NULL,           -- JSON dry-run: golden values this version would change
    PRIMARY KEY (entity, version)
);

CREATE TABLE policy_rule (
    entity          TEXT NOT NULL,
    version         INTEGER NOT NULL,
    field           TEXT NOT NULL,
    strategy        TEXT NOT NULL,        -- source_priority | most_recent | most_complete | owner_decision
    sources         TEXT,                 -- JSON ordered list
    timestamp_field TEXT,
    tie_breaker     TEXT NOT NULL,
    owner           TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    PRIMARY KEY (entity, version, field)
);

CREATE TABLE golden_attribute (
    entity         TEXT NOT NULL,
    cluster_id     TEXT NOT NULL,
    field          TEXT NOT NULL,
    value          TEXT,                  -- NULL when unresolved
    unit           TEXT,
    status         TEXT NOT NULL,         -- resolved | unresolved
    source         TEXT,
    record_id      TEXT,
    source_field   TEXT,
    rule           TEXT,
    origin         TEXT,                  -- rule | owner_decision
    policy_version INTEGER NOT NULL,
    source_ts      TEXT,
    selected_at    TIMESTAMP NOT NULL,
    PRIMARY KEY (entity, cluster_id, field)
);

CREATE TABLE conflict (
    conflict_id   TEXT PRIMARY KEY,
    entity        TEXT NOT NULL,
    cluster_id    TEXT NOT NULL,
    field         TEXT NOT NULL,
    candidates    TEXT NOT NULL,          -- JSON [{source, record_id, value}] only what the decision needs
    detected_at   TIMESTAMP NOT NULL,
    owner         TEXT NOT NULL,
    due_date      DATE NOT NULL,
    status        TEXT NOT NULL,          -- open | resolved | accepted_deviation | reopened | overridden
    personal_data BOOLEAN NOT NULL,
    decision      TEXT,                   -- chosen or corrected value
    decision_src  TEXT,
    ticket        TEXT,                   -- write-back ticket reference
    reason        TEXT,
    approver      TEXT,
    decided_at    TIMESTAMP,
    decided_run   TEXT,                   -- latest normalize run at resolution time (reopen detection)
    prev_status   TEXT
);

CREATE TABLE standardize_metric (         -- read by readiness-scorecard (c03) and freshness-monitoring (c09)
    metric_id   TEXT PRIMARY KEY,
    run_id      TEXT,
    kind        TEXT NOT NULL,            -- conformance | er | policy_coverage | conflicts
    entity      TEXT,
    field       TEXT,
    name        TEXT NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMP NOT NULL
);
