-- c06-structure-and-enrich: chunking, metadata envelope, column annotations, business glossary.
-- Shared DDL for Postgres 16 and DuckDB. Decisions go to audit_event (step 'enrich'), the shared decision trail.
-- Rollback: 006_c06_structure_and_enrich.down.sql. New tables only; source data is never touched.

CREATE TABLE chunking_profile (           -- audited per-category overrides of the default profile
    charter_id  TEXT NOT NULL,
    category_id TEXT NOT NULL,
    settings    TEXT NOT NULL,            -- JSON overrides only, e.g. {"max_tokens": 600}
    updated_at  TIMESTAMP NOT NULL,
    updated_by  TEXT NOT NULL,
    PRIMARY KEY (charter_id, category_id)
);

CREATE TABLE chunk_run (
    run_id      TEXT PRIMARY KEY,
    charter_id  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    config      TEXT NOT NULL,            -- JSON {profiles: {category: profile}, tools: {name: version}}
    inputs      TEXT NOT NULL,            -- JSON {item_id: source_version}
    report      TEXT NOT NULL,            -- JSON chunking report
    status      TEXT NOT NULL             -- done | rolled_back
);

CREATE TABLE chunk_set (                  -- the chunk set a run made active for one item (rollback = earlier set)
    run_id         TEXT NOT NULL,
    item_id        TEXT NOT NULL,
    source_version TEXT NOT NULL,
    status         TEXT NOT NULL,         -- chunked | needs-review
    error          TEXT,
    chunk_ids      TEXT NOT NULL,         -- JSON list in document order
    created_at     TIMESTAMP NOT NULL,
    PRIMARY KEY (run_id, item_id)
);

CREATE TABLE chunk (
    chunk_id       TEXT PRIMARY KEY,      -- sha256(item_id, heading_path, content_hash, ordinal)
    item_id        TEXT NOT NULL,
    source_version TEXT NOT NULL,         -- source version the chunk was first cut from
    first_run      TEXT NOT NULL,
    heading_path   TEXT NOT NULL,         -- JSON list of ancestor headings
    ordinal        INTEGER NOT NULL,      -- occurrence of identical content under the same heading path
    kind           TEXT NOT NULL,         -- text | table
    method         TEXT NOT NULL,         -- structure | fallback
    content        TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    tokens         INTEGER NOT NULL,
    location       TEXT NOT NULL,         -- JSON {pages: [...]} or {elements: [first, last]}
    position       INTEGER NOT NULL,      -- order inside the document
    status         TEXT NOT NULL,         -- active | superseded | inactive
    superseded_by  TEXT,                  -- source version that replaced this chunk
    created_at     TIMESTAMP NOT NULL
);

CREATE TABLE enrich_run (
    run_id     TEXT PRIMARY KEY,
    charter_id TEXT NOT NULL,
    actor      TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    config     TEXT NOT NULL,             -- JSON
    report     TEXT NOT NULL,             -- JSON enrichment report
    status     TEXT NOT NULL              -- done | rolled_back
);

CREATE TABLE chunk_metadata (             -- the metadata envelope of one chunk or one agent-exposed table
    subject_id        TEXT PRIMARY KEY,   -- chunk_id or table asset_uri
    subject_kind      TEXT NOT NULL,      -- chunk | table
    item_id           TEXT,
    run_id            TEXT NOT NULL,
    envelope          TEXT NOT NULL,      -- JSON: all envelope fields
    source_id         TEXT,
    content_type      TEXT,
    owner             TEXT,
    sensitivity       TEXT,
    authority         TEXT,
    freshness_class   TEXT,
    acl_tags          TEXT,               -- JSON {allow: [...], deny: [...]}
    acl_stale         BOOLEAN NOT NULL,
    expired           BOOLEAN NOT NULL,
    stale_after       TEXT,
    glossary_terms    TEXT NOT NULL,      -- JSON list of term IDs
    context_header    TEXT NOT NULL,
    generated_context TEXT,               -- JSON {text, model, label}
    complete          BOOLEAN NOT NULL,   -- every mandatory field present
    status            TEXT NOT NULL,      -- ready-for-agents | needs-review
    reasons           TEXT NOT NULL,      -- JSON list
    updated_at        TIMESTAMP NOT NULL
);

CREATE TABLE chunk_metadata_history (     -- previous envelope rows per run, for rollback
    run_id     TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    previous   TEXT,                      -- JSON row, NULL when the run created it
    PRIMARY KEY (run_id, subject_id)
);

CREATE TABLE column_annotation (
    table_id       TEXT NOT NULL,         -- asset_uri of the table
    column_name    TEXT NOT NULL,
    exposed        BOOLEAN NOT NULL,
    description    TEXT,
    data_type      TEXT,
    unit           TEXT,
    allowed_values TEXT,                  -- JSON list
    format         TEXT,
    nullable       BOOLEAN,
    term           TEXT,                  -- glossary term ID or preferred label
    updated_by     TEXT NOT NULL,
    updated_at     TIMESTAMP NOT NULL,
    PRIMARY KEY (table_id, column_name)
);

CREATE TABLE glossary (
    glossary_id TEXT PRIMARY KEY,         -- = charter_id: one pilot glossary
    stewards    TEXT NOT NULL,            -- JSON list of stakeholder IDs
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL
);

CREATE TABLE glossary_term (
    term_id         TEXT PRIMARY KEY,
    glossary_id     TEXT NOT NULL,
    exposed_version INTEGER,              -- the approved version downstream interfaces see
    deprecated      BOOLEAN NOT NULL,
    replaced_by     TEXT,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE glossary_term_version (
    term_id     TEXT NOT NULL,
    version     INTEGER NOT NULL,
    status      TEXT NOT NULL,            -- draft | in_review | approved | rejected | deprecated
    content     TEXT NOT NULL,            -- JSON {labels, definition, scope_note, examples, owner, category, ...}
    authors     TEXT NOT NULL,            -- JSON list: nobody listed here may approve this version
    ai_drafted  TEXT,                     -- JSON {model} when a language model drafted text
    created_at  TIMESTAMP NOT NULL,
    decided_by  TEXT,
    decided_at  TIMESTAMP,
    comment     TEXT,
    PRIMARY KEY (term_id, version)
);

CREATE TABLE glossary_link (
    term_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- column | chunk | metric
    target      TEXT NOT NULL,            -- table#column, chunk_id or term_id of the metric
    flagged     BOOLEAN NOT NULL,         -- part of an open conflict
    updated_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (term_id, kind, target)
);

CREATE TABLE glossary_metric (            -- metric definitions of approved metric terms (MetricFlow-like shape)
    term_id     TEXT NOT NULL,
    version     INTEGER NOT NULL,
    formula     TEXT NOT NULL,            -- formula or source query
    grain       TEXT NOT NULL,
    unit        TEXT NOT NULL,
    owner       TEXT NOT NULL,
    approved_at TIMESTAMP NOT NULL,
    PRIMARY KEY (term_id, version)
);

-- Interface for c07 (access-control, masking), c08 (data products, MCP) and c09 (evaluation, freshness).
CREATE VIEW agent_chunk AS
    SELECT c.chunk_id, c.item_id, c.heading_path, c.kind, c.method, c.content, c.tokens, c.location,
           m.envelope, m.context_header, m.acl_tags, m.acl_stale, m.sensitivity, m.authority, m.freshness_class,
           m.stale_after, m.expired, m.glossary_terms, m.status
    FROM chunk c JOIN chunk_metadata m ON m.subject_id = c.chunk_id
    WHERE c.status = 'active';

CREATE VIEW chunk_acl AS
    SELECT subject_id, subject_kind, item_id, acl_tags, acl_stale FROM chunk_metadata;

CREATE VIEW metadata_completeness AS
    SELECT content_type AS category_id, subject_kind, COUNT(*) AS subjects,
           SUM(CASE WHEN complete THEN 1 ELSE 0 END) AS complete,
           SUM(CASE WHEN status = 'ready-for-agents' THEN 1 ELSE 0 END) AS ready
    FROM chunk_metadata GROUP BY content_type, subject_kind;

CREATE VIEW approved_glossary_term AS
    SELECT t.term_id, t.glossary_id, v.version, t.deprecated, t.replaced_by, v.content
    FROM glossary_term t JOIN glossary_term_version v ON v.term_id = t.term_id AND v.version = t.exposed_version
    WHERE t.exposed_version IS NOT NULL;
