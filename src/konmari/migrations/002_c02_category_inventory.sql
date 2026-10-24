-- c02-category-inventory: source inventory and category map. Shared DDL for Postgres 16 and DuckDB.
-- Decisions go to audit_event (c01). Rollback: 002_c02_category_inventory.down.sql.

CREATE TABLE inventory_source (
    source_id       TEXT PRIMARY KEY,     -- stable charter source ID; renames keep it
    charter_id      TEXT NOT NULL,
    charter_version INTEGER NOT NULL,
    connector       TEXT NOT NULL,        -- sharepoint | sql | fileshare | csv-import
    locator         TEXT NOT NULL,
    credential_ref  TEXT,                 -- secret reference name only, never the value
    title           TEXT,
    registered_at   TIMESTAMP NOT NULL,
    registered_by   TEXT NOT NULL,
    state           TEXT                  -- JSON connector state for incremental rescans
);

CREATE TABLE inventory_run (
    run_id         TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL,
    started_at     TIMESTAMP NOT NULL,
    finished_at    TIMESTAMP,
    actor          TEXT NOT NULL,
    sample_size    INTEGER NOT NULL,
    assets_seen    INTEGER,
    assets_missing INTEGER,
    status         TEXT NOT NULL          -- running | done | failed
);

CREATE TABLE source_asset (
    asset_uri                TEXT PRIMARY KEY,
    source_id                TEXT NOT NULL,
    charter_id               TEXT NOT NULL,
    system_type              TEXT NOT NULL,
    origin                   TEXT NOT NULL, -- scan | manual
    title                    TEXT NOT NULL,
    path                     TEXT,
    owner                    TEXT,
    steward                  TEXT,
    update_frequency         TEXT NOT NULL, -- ISO 8601 duration | continuous | irregular | unknown
    last_modified            TIMESTAMP,
    format                   TEXT,
    size                     BIGINT,
    sensitivity_tier         TEXT,
    tier_state               TEXT,          -- proposed | confirmed
    personal_data_categories TEXT,          -- JSON array
    ropa_ref                 TEXT,
    ropa_missing             BOOLEAN NOT NULL,
    access_rights_note       TEXT,
    content_hash             TEXT,
    schema_hash              TEXT,
    schema_fields            TEXT,          -- JSON [{name, type}]
    first_seen               TIMESTAMP NOT NULL,
    last_seen                TIMESTAMP NOT NULL,
    status                   TEXT NOT NULL, -- complete | incomplete | missing
    missing_fields           TEXT,          -- JSON [{field, reason}]
    previous_status          TEXT,
    missing_since            TIMESTAMP,
    waiver                   TEXT           -- JSON {by, reason, at}
);

CREATE TABLE sensitivity_hint (
    asset_uri   TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    hit_count   INTEGER NOT NULL,
    max_score   DOUBLE PRECISION NOT NULL,
    sample_size INTEGER NOT NULL,
    scanned_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (asset_uri, entity_type)
);

CREATE TABLE category (
    charter_id    TEXT NOT NULL,
    category_id   TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    baseline_tier TEXT NOT NULL,
    status        TEXT NOT NULL,         -- active | retired
    PRIMARY KEY (charter_id, category_id)
);

CREATE TABLE asset_category (
    asset_uri   TEXT NOT NULL,
    category_id TEXT NOT NULL,
    is_primary  BOOLEAN NOT NULL,
    state       TEXT NOT NULL,           -- suggested | accepted | rejected
    rule_id     TEXT,
    matched     TEXT,
    updated_at  TIMESTAMP NOT NULL,
    updated_by  TEXT NOT NULL,
    PRIMARY KEY (asset_uri, category_id)
);

CREATE TABLE category_order_version (
    charter_id  TEXT NOT NULL,
    version     INTEGER NOT NULL,
    status      TEXT NOT NULL,           -- proposed | approved
    categories  TEXT NOT NULL,           -- JSON array, first = tidied first
    scores      TEXT NOT NULL,           -- JSON {category: {score, inputs}}
    weights     TEXT NOT NULL,           -- JSON
    reason      TEXT,
    created_at  TIMESTAMP NOT NULL,
    created_by  TEXT NOT NULL,
    approved_by TEXT,
    approved_at TIMESTAMP,
    PRIMARY KEY (charter_id, version)
);

CREATE TABLE category_step (
    charter_id    TEXT NOT NULL,
    category_id   TEXT NOT NULL,
    step          TEXT NOT NULL,         -- inventory | score | discard | standardize | enrich | govern | connect | evaluate
    status        TEXT NOT NULL,         -- not_started | in_progress | complete
    updated_at    TIMESTAMP NOT NULL,
    updated_by    TEXT NOT NULL,
    waiver        TEXT,                  -- JSON {by, reason, at}: may start before earlier categories finish this step
    PRIMARY KEY (charter_id, category_id, step)
);
