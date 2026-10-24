-- c07-governance: ACLs on items, chunks and records, the governed agent index, source policies, write grants,
-- PII findings and gate results, leak-suite runs, governance status, pseudonym key in its own schema.
-- Shared DDL for Postgres 16 and DuckDB; RLS, GIN indexes, roles and audit partitioning are in .postgres.sql.
-- Rollback: 007_c07_governance.down.sql (+ .down.postgres.sql). Decisions go to audit_event (step 'govern').

-- ACL columns (access-control "Source ACL capture"): acl_status known | policy | unknown; NULL = never synced.
ALTER TABLE corpus_item ADD COLUMN allowed_principals TEXT[];
ALTER TABLE corpus_item ADD COLUMN denied_principals TEXT[];
ALTER TABLE corpus_item ADD COLUMN acl_version TEXT;
ALTER TABLE corpus_item ADD COLUMN acl_status TEXT;
ALTER TABLE chunk ADD COLUMN allowed_principals TEXT[];
ALTER TABLE chunk ADD COLUMN denied_principals TEXT[];
ALTER TABLE chunk ADD COLUMN acl_version TEXT;
ALTER TABLE chunk ADD COLUMN acl_status TEXT;
ALTER TABLE golden_attribute ADD COLUMN allowed_principals TEXT[];
ALTER TABLE golden_attribute ADD COLUMN denied_principals TEXT[];
ALTER TABLE golden_attribute ADD COLUMN acl_version TEXT;
ALTER TABLE golden_attribute ADD COLUMN acl_status TEXT;

CREATE TABLE agent_index (                -- the only agent-facing chunk index: masked content, ACL on every row
    chunk_id           TEXT PRIMARY KEY,
    item_id            TEXT NOT NULL,
    category_id        TEXT NOT NULL,
    content            TEXT NOT NULL,     -- masked per source policy; never the original
    sensitivity        TEXT NOT NULL,
    allowed_principals TEXT[] NOT NULL,
    denied_principals  TEXT[] NOT NULL,
    acl_version        TEXT NOT NULL,
    policy_version     INTEGER NOT NULL,
    embedding          DOUBLE PRECISION[], -- optional; filled by the embedding step of c08/c09
    indexed_at         TIMESTAMP NOT NULL
);

CREATE TABLE source_policy (
    policy_id     TEXT NOT NULL,          -- = category ID: one policy per in-scope category
    version       INTEGER NOT NULL,
    status        TEXT NOT NULL,          -- pending | approved | superseded
    content       TEXT NOT NULL,          -- JSON: exposure, sensitivity, masking, acl_sync_cadence, tools, owner, ...
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL,
    reason        TEXT,
    approved_by   TEXT,
    approved_at   TIMESTAMP,
    reverted_from INTEGER,
    PRIMARY KEY (policy_id, version)
);

CREATE TABLE agent_client (               -- registered agents (OAuth clients) and their least-privilege scopes
    agent_id          TEXT PRIMARY KEY,
    scopes            TEXT NOT NULL,      -- JSON list: read:<category> | write:<category>
    service_principal BOOLEAN NOT NULL,   -- autonomous agent without an on-behalf-of user
    registered_by     TEXT NOT NULL,
    registered_at     TIMESTAMP NOT NULL
);

CREATE TABLE write_grant (
    grant_id    TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    category_id TEXT NOT NULL,
    tool        TEXT NOT NULL,
    approved_by TEXT NOT NULL,            -- the category's data owner
    approved_at TIMESTAMP NOT NULL,
    expires_at  TIMESTAMP NOT NULL,
    revoked_at  TIMESTAMP
);

CREATE TABLE acl_sync (
    sync_id     TEXT PRIMARY KEY,
    policy_id   TEXT NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    status      TEXT NOT NULL,            -- ok | failed
    lag_seconds DOUBLE PRECISION,         -- time since the previous successful sync
    items       INTEGER NOT NULL,
    unknown     INTEGER NOT NULL,
    revocations INTEGER NOT NULL,
    error       TEXT
);

CREATE TABLE pii_scan (                   -- one completed scan per chunk content and detector version
    chunk_id         TEXT PRIMARY KEY,
    content_hash     TEXT NOT NULL,
    language         TEXT,
    detector_version TEXT NOT NULL,
    findings         INTEGER NOT NULL,
    scanned_at       TIMESTAMP NOT NULL
);

CREATE TABLE pii_finding (                -- entity type and location only; never the detected value
    finding_id       TEXT PRIMARY KEY,
    chunk_id         TEXT NOT NULL,
    entity_type      TEXT NOT NULL,
    start_pos        INTEGER NOT NULL,
    end_pos          INTEGER NOT NULL,
    score            DOUBLE PRECISION NOT NULL,
    detector_version TEXT NOT NULL,
    review_state     TEXT NOT NULL,       -- auto | pending | confirmed | rejected
    reviewed_by      TEXT,
    reviewed_at      TIMESTAMP
);

CREATE TABLE pii_label (                  -- human labels of the recall sample, as offsets (no values)
    sample_id   TEXT NOT NULL,
    chunk_id    TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    start_pos   INTEGER NOT NULL,
    end_pos     INTEGER NOT NULL
);

CREATE TABLE pii_gate_result (
    result_id        TEXT PRIMARY KEY,
    policy_id        TEXT NOT NULL,
    sample_id        TEXT NOT NULL,
    sample_size      INTEGER NOT NULL,
    detector_version TEXT NOT NULL,
    recall           TEXT NOT NULL,       -- JSON {entity: {labelled, detected, recall}}
    threshold        DOUBLE PRECISION NOT NULL,
    passed           BOOLEAN NOT NULL,
    missed           TEXT NOT NULL,       -- JSON list of references chunk_id:start-end:entity
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMP NOT NULL
);

CREATE TABLE leak_run (
    run_id          TEXT PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    actor           TEXT NOT NULL,
    passed          BOOLEAN NOT NULL,
    queries         INTEGER NOT NULL,
    failures        TEXT NOT NULL,        -- JSON list {principal, chunk_id, category_id}
    policy_versions TEXT NOT NULL         -- JSON {category: version}
);

CREATE TABLE governance_check (           -- computed by `konmari govern check`, never set by hand
    check_id       TEXT NOT NULL,
    policy_id      TEXT NOT NULL,
    status         TEXT NOT NULL,         -- approved | blocked | acl_stale
    reasons        TEXT NOT NULL,         -- JSON list
    policy_version INTEGER,
    checked_at     TIMESTAMP NOT NULL,
    PRIMARY KEY (check_id, policy_id)
);

CREATE VIEW governance_status AS          -- latest computed status per source (category); read by c08
    SELECT g.policy_id AS category_id, g.status, g.reasons, g.policy_version, g.checked_at
    FROM governance_check g
    WHERE g.checked_at = (SELECT MAX(checked_at) FROM governance_check x WHERE x.policy_id = g.policy_id);

CREATE SCHEMA konmari_keys;               -- only the HMAC key (no reverse lookup table, ROADMAP question 10)
CREATE TABLE konmari_keys.pseudonym_key (
    key_id     TEXT PRIMARY KEY,
    secret     TEXT NOT NULL,
    active     BOOLEAN NOT NULL,
    created_at TIMESTAMP NOT NULL
);
