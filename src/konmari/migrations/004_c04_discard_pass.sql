-- c04-discard-pass: items, detection, calibration, proposals, archive, retention. Shared DDL for Postgres 16 and DuckDB.
-- Decisions go to the hash-chained audit_event trail (c01); decision_event is its discard-step view.
-- Rollback: 004_c04_discard_pass.down.sql (export the decision log first: konmari discard log export).

ALTER TABLE audit_event ADD COLUMN seq BIGINT;          -- chain position; NULL = written before the chain existed
ALTER TABLE audit_event ADD COLUMN prev_hash TEXT;
ALTER TABLE audit_event ADD COLUMN hash TEXT;           -- SHA-256(prev_hash + canonical JSON of the event)

CREATE VIEW decision_event AS SELECT * FROM audit_event WHERE step = 'discard';

CREATE TABLE corpus_item (                -- one document, file or record inside an inventoried asset
    item_id          TEXT PRIMARY KEY,
    asset_uri        TEXT NOT NULL,
    charter_id       TEXT NOT NULL,
    title            TEXT NOT NULL,
    content_ref      TEXT NOT NULL,       -- where the bytes are read from (source location, never modified)
    text_ref         TEXT,                -- optional pre-extracted text (e.g. for PDFs)
    meta             TEXT NOT NULL,       -- JSON source metadata: version, logical_id, effective_date, ...
    state            TEXT NOT NULL,       -- active | archived | excluded | awaiting-masking | restricted
    state_changed_at TIMESTAMP NOT NULL,
    restricted_until DATE,
    masked_ref       TEXT,                -- set by sensitive-data-masking (c07)
    first_seen       TIMESTAMP NOT NULL,
    last_seen        TIMESTAMP NOT NULL
);

CREATE VIEW kept_item AS SELECT * FROM corpus_item WHERE state = 'active';

CREATE TABLE discard_run (
    run_id             TEXT PRIMARY KEY,
    charter_id         TEXT NOT NULL,
    charter_version    INTEGER NOT NULL,
    inventory_snapshot TEXT NOT NULL,
    config             TEXT NOT NULL,     -- JSON detector configuration
    actor              TEXT NOT NULL,
    started_at         TIMESTAMP NOT NULL,
    finished_at        TIMESTAMP,
    stats              TEXT,              -- JSON counts
    report             TEXT               -- JSON run report
);

CREATE TABLE fingerprint (
    run_id       TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    text_sha256  TEXT NOT NULL,
    raw_sha256   TEXT NOT NULL,
    norm_version TEXT NOT NULL,
    length       INTEGER NOT NULL,
    PRIMARY KEY (run_id, item_id)
);

CREATE TABLE candidate_pair (             -- every verified candidate, also below the threshold (calibration input)
    run_id   TEXT NOT NULL,
    detector TEXT NOT NULL,               -- minhash | containment | semantic
    item_a   TEXT NOT NULL,
    item_b   TEXT NOT NULL,
    score    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, detector, item_a, item_b)
);

CREATE TABLE flag (                       -- evidence: IDs, scores, parameters, signals; never text excerpts
    flag_id     TEXT PRIMARY KEY,
    run_id      TEXT,                     -- NULL for reviewer flags
    flag_type   TEXT NOT NULL,            -- duplicate | superseded | off-scope | sensitive-review | review
    method      TEXT NOT NULL,            -- exact | minhash | containment | semantic | version-history | ...
    item_id     TEXT NOT NULL,
    related_ids TEXT NOT NULL,            -- JSON array
    score       DOUBLE PRECISION,
    params      TEXT NOT NULL,            -- JSON
    signals     TEXT NOT NULL,            -- JSON
    cluster_id  TEXT,
    created_at  TIMESTAMP NOT NULL,
    created_by  TEXT NOT NULL
);

CREATE TABLE cluster (
    cluster_id       TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    family           TEXT NOT NULL,       -- exact | lexical | semantic
    members          TEXT NOT NULL,       -- JSON array of item IDs
    canonical_item   TEXT NOT NULL,
    canonical_reason TEXT NOT NULL        -- source-of-truth | effective-date | source-version | completeness
);

CREATE TABLE calibration_label (
    label_id   TEXT PRIMARY KEY,
    detector   TEXT NOT NULL,
    item_a     TEXT NOT NULL,
    item_b     TEXT NOT NULL,
    score      DOUBLE PRECISION NOT NULL,
    label      TEXT NOT NULL,             -- duplicate | related-but-distinct | unrelated
    labeled_by TEXT NOT NULL,
    labeled_at TIMESTAMP NOT NULL
);

CREATE TABLE calibration (                -- selected threshold or named override per detector
    calibration_id TEXT PRIMARY KEY,
    charter_id     TEXT NOT NULL,
    detector       TEXT NOT NULL,
    threshold      DOUBLE PRECISION,
    labels         INTEGER NOT NULL,
    metrics        TEXT NOT NULL,         -- JSON {threshold: {precision, recall}}
    override_reason TEXT,
    decided_by     TEXT NOT NULL,
    decided_at     TIMESTAMP NOT NULL
);

CREATE TABLE proposal (
    proposal_id     TEXT PRIMARY KEY,
    charter_id      TEXT NOT NULL,
    run_id          TEXT,
    item_id         TEXT NOT NULL,
    category_id     TEXT,
    action          TEXT NOT NULL,        -- keep | archive | exclude | route-to-masking | erase
    method          TEXT NOT NULL,        -- flag method the proposal rests on (bulk approval groups by it)
    flag_ids        TEXT NOT NULL,        -- JSON array
    reason          TEXT NOT NULL,
    proposed_by     TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    status          TEXT NOT NULL,        -- pending | approved | rejected | scheduled | executed | cancelled
    decided_action  TEXT,
    decided_by      TEXT,
    decided_at      TIMESTAMP,
    comment         TEXT,
    compliance_by   TEXT,
    compliance_at   TIMESTAMP,
    execute_after   TIMESTAMP,
    executed_at     TIMESTAMP
);

CREATE TABLE archive_manifest (
    item_id           TEXT PRIMARY KEY,
    proposal_id       TEXT NOT NULL,
    original_location TEXT NOT NULL,
    object_key        TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    retention_rule_id TEXT,
    archived_at       TIMESTAMP NOT NULL
);

CREATE TABLE derived_artifact (           -- chunks, embeddings, data-product rows registered by c06/c08
    artifact_id          TEXT PRIMARY KEY,
    item_id              TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    registered_at        TIMESTAMP NOT NULL,
    marked_for_removal_at TIMESTAMP,
    removed_at           TIMESTAMP
);

CREATE TABLE retention_rule (
    rule_id       TEXT PRIMARY KEY,
    charter_id    TEXT NOT NULL,
    category_id   TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    legal_basis   TEXT NOT NULL,
    source        TEXT NOT NULL,
    start_event   TEXT NOT NULL,          -- last-entry | end-of-calendar-year-of-last-entry
    period        TEXT NOT NULL,          -- ISO 8601 duration, e.g. P8Y
    expiry_action TEXT NOT NULL,          -- erase | review
    status        TEXT NOT NULL,          -- proposed | approved
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL,
    approver      TEXT,
    approved_at   TIMESTAMP
);

CREATE TABLE legal_hold (
    hold_id     TEXT PRIMARY KEY,
    charter_id  TEXT NOT NULL,
    item_id     TEXT,
    category_id TEXT,
    reason      TEXT NOT NULL,
    owner       TEXT NOT NULL,
    review_date DATE NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    released_at TIMESTAMP
);

CREATE TABLE erasure_record (             -- no content and no personal data
    item_id      TEXT PRIMARY KEY,
    charter_id   TEXT NOT NULL,
    category_id  TEXT,
    rule_id      TEXT NOT NULL,
    approvers    TEXT NOT NULL,           -- JSON [category approver, compliance approver]
    erased_at    TIMESTAMP NOT NULL,
    open_actions TEXT NOT NULL            -- JSON [{owner, action}] e.g. delete at the source location
);

CREATE TABLE retention_check_run (
    run_id      TEXT PRIMARY KEY,
    charter_id  TEXT NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    window_days INTEGER NOT NULL,
    schedule    TEXT NOT NULL,            -- ISO 8601 duration between checks
    proposals   INTEGER
);
