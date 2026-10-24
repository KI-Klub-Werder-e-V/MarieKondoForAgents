-- c01-pilot-charter: readiness catalog core. Shared DDL subset for Postgres 16 and DuckDB.

CREATE TABLE charter_version (
    charter_id   TEXT NOT NULL,
    version      INTEGER NOT NULL,
    status       TEXT NOT NULL,          -- draft | approved
    content      TEXT NOT NULL,          -- canonical JSON
    content_hash TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL,
    created_by   TEXT NOT NULL,
    reason       TEXT,
    PRIMARY KEY (charter_id, version)
);

CREATE TABLE stakeholder (
    charter_id     TEXT NOT NULL,
    version        INTEGER NOT NULL,
    stakeholder_id TEXT NOT NULL,
    name           TEXT NOT NULL,
    contact        TEXT NOT NULL,
    org_unit       TEXT NOT NULL,
    roles          TEXT NOT NULL,        -- comma-separated role vocabulary
    PRIMARY KEY (charter_id, version, stakeholder_id)
);

CREATE TABLE raci_assignment (
    charter_id     TEXT NOT NULL,
    version        INTEGER NOT NULL,
    activity       TEXT NOT NULL,
    stakeholder_id TEXT NOT NULL,
    raci           TEXT NOT NULL,        -- R | A | C | I
    PRIMARY KEY (charter_id, version, activity, stakeholder_id, raci)
);

CREATE TABLE metric_set_version (
    charter_id   TEXT NOT NULL,
    version      INTEGER NOT NULL,
    status       TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL,
    created_by   TEXT NOT NULL,
    reason       TEXT,
    flags        TEXT,                   -- e.g. target_lowered_after_measurement:<metric ids>
    PRIMARY KEY (charter_id, version)
);

CREATE TABLE metric_definition (
    charter_id         TEXT NOT NULL,
    metric_set_version INTEGER NOT NULL,
    metric_id          TEXT NOT NULL,
    name               TEXT NOT NULL,
    family             TEXT NOT NULL,
    unit               TEXT NOT NULL,
    direction          TEXT NOT NULL,
    target             DOUBLE PRECISION NOT NULL,
    tier               TEXT NOT NULL,
    owner              TEXT NOT NULL,
    cadence            TEXT NOT NULL,
    PRIMARY KEY (charter_id, metric_set_version, metric_id)
);

CREATE TABLE metric_measurement (
    id                 TEXT PRIMARY KEY,
    charter_id         TEXT NOT NULL,
    metric_id          TEXT NOT NULL,
    kind               TEXT NOT NULL,    -- baseline | gate | final
    value              DOUBLE PRECISION, -- NULL = not_yet_measured (baseline only)
    unit               TEXT,
    measured_on        DATE,
    plan_date          DATE,
    method             TEXT,
    author             TEXT NOT NULL,
    step               TEXT,
    charter_version    INTEGER NOT NULL,
    metric_set_version INTEGER NOT NULL,
    golden_set_hash    TEXT,
    recorded_at        TIMESTAMP NOT NULL
);

CREATE TABLE approval (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,          -- charter | metrics | ai_act_high
    charter_id   TEXT NOT NULL,
    version      INTEGER NOT NULL,
    approver_id  TEXT NOT NULL,
    role         TEXT NOT NULL,
    os_user      TEXT NOT NULL,
    approved_at  TIMESTAMP NOT NULL,
    content_hash TEXT NOT NULL,
    note         TEXT
);

CREATE TABLE decision_log (
    id              TEXT PRIMARY KEY,
    charter_id      TEXT NOT NULL,
    charter_version INTEGER NOT NULL,
    logged_at       TIMESTAMP NOT NULL,
    author          TEXT NOT NULL,
    meeting_type    TEXT NOT NULL,       -- kickoff | working | steering | phase_gate | post_pilot | other
    meeting_date    DATE NOT NULL,
    attendees       TEXT NOT NULL,       -- JSON array
    decisions       TEXT NOT NULL,       -- JSON array
    actions         TEXT NOT NULL        -- JSON array of {owner, action}
);

-- Append-only: the catalog layer refuses UPDATE/DELETE; Postgres also has a trigger (see .postgres.sql).
CREATE TABLE audit_event (
    id              TEXT PRIMARY KEY,
    ts              TIMESTAMP NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    charter_id      TEXT,
    charter_version INTEGER,
    step            TEXT,
    result          TEXT,
    details         TEXT                 -- JSON
);
