-- c08-connect-agent-interfaces: data products (ODPS + ODCS descriptors), versions with their state machine,
-- owner approvals, contract test results, refresh runs with change counts, materialised product rows,
-- OpenLineage run events and the reviewed tool-definition pins of the MCP server.
-- Shared DDL for Postgres 16 and DuckDB. Rollback: 008_c08_connect_agent_interfaces.down.sql.
-- Decisions go to audit_event (step 'connect').

CREATE TABLE data_product (
    product_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category_id  TEXT NOT NULL,           -- c02 category, governed by the c07 source policy of the same ID
    owner        TEXT NOT NULL,           -- accountable owner (stakeholder ID)
    charter_id   TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL
);

CREATE TABLE data_product_version (
    product_id    TEXT NOT NULL,
    version       TEXT NOT NULL,          -- semver MAJOR.MINOR.PATCH
    state         TEXT NOT NULL,          -- draft | approved | published | superseded | deprecated | sunset | withdrawn
    descriptor    TEXT NOT NULL,          -- JSON {"product": ODPS, "contract": ODCS}
    content_hash  TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL,
    created_by    TEXT NOT NULL,
    published_at  TIMESTAMP,
    deprecated_at TIMESTAMP,
    sunset_at     TIMESTAMP,
    successor     TEXT,
    PRIMARY KEY (product_id, version)
);

CREATE TABLE product_approval (
    approval_id   TEXT PRIMARY KEY,
    product_id    TEXT NOT NULL,
    version       TEXT NOT NULL,
    kind          TEXT NOT NULL,          -- publish | rollback
    approver      TEXT NOT NULL,
    approved_at   TIMESTAMP NOT NULL,
    diff_summary  TEXT NOT NULL,          -- JSON {"bump", "required", "breaking", "changes"}
    test_id       TEXT,
    content_hash  TEXT NOT NULL,
    reason        TEXT
);

CREATE TABLE product_test (
    test_id      TEXT PRIMARY KEY,
    product_id   TEXT NOT NULL,
    version      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    run_at       TIMESTAMP NOT NULL,
    passed       BOOLEAN NOT NULL,
    row_count    INTEGER NOT NULL,
    failures     TEXT NOT NULL,           -- JSON [{"rule", "field", "violations"}]
    origin       TEXT NOT NULL            -- test | refresh
);

CREATE TABLE product_row (                -- the published, materialised product data (source values, unmasked;
    product_id   TEXT NOT NULL,           -- masking and row filters are applied per caller at the tool boundary)
    row_key      TEXT NOT NULL,
    data         TEXT NOT NULL,           -- JSON of the contract fields only
    acl          TEXT NOT NULL,           -- JSON {"allow": [...], "deny": [...]}
    cursor_value TEXT,
    PRIMARY KEY (product_id, row_key)
);

CREATE TABLE refresh_run (
    run_id       TEXT PRIMARY KEY,
    product_id   TEXT NOT NULL,
    started_at   TIMESTAMP NOT NULL,
    ended_at     TIMESTAMP,
    status       TEXT NOT NULL,           -- running | ok | failed
    mode         TEXT NOT NULL,           -- cursor
    cursor_from  TEXT,
    cursor_to    TEXT,                    -- source high-water mark after the run
    inserted     INTEGER NOT NULL,
    updated      INTEGER NOT NULL,
    deleted      INTEGER NOT NULL,
    as_of        TIMESTAMP,               -- product as_of after the run (unchanged on failure)
    test_id      TEXT,
    error        TEXT
);

CREATE TABLE lineage_event (
    event_id     TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    job          TEXT NOT NULL,           -- product.refresh | product.publish
    event_type   TEXT NOT NULL,           -- START | COMPLETE | FAIL
    event_time   TIMESTAMP NOT NULL,
    event        TEXT NOT NULL            -- the OpenLineage RunEvent JSON
);

CREATE TABLE tool_pin (                   -- reviewed MCP tool definitions; a tool is exposed only on a hash match
    tool_name    TEXT PRIMARY KEY,
    def_hash     TEXT NOT NULL,           -- SHA-256 of the canonical JSON of the full definition
    approved_by  TEXT NOT NULL,
    approved_at  TIMESTAMP NOT NULL
);
