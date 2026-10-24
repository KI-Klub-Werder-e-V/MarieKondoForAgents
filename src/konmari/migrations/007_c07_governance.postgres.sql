-- Postgres-only hardening for c07: GIN indexes, RLS on the agent index, roles, key schema, audit partitioning.

CREATE INDEX corpus_item_allowed_gin ON corpus_item USING GIN (allowed_principals);
CREATE INDEX corpus_item_denied_gin ON corpus_item USING GIN (denied_principals);
CREATE INDEX chunk_allowed_gin ON chunk USING GIN (allowed_principals);
CREATE INDEX chunk_denied_gin ON chunk USING GIN (denied_principals);
CREATE INDEX golden_attribute_allowed_gin ON golden_attribute USING GIN (allowed_principals);
CREATE INDEX golden_attribute_denied_gin ON golden_attribute USING GIN (denied_principals);
CREATE INDEX agent_index_allowed_gin ON agent_index USING GIN (allowed_principals);
CREATE INDEX agent_index_denied_gin ON agent_index USING GIN (denied_principals);
ALTER TABLE agent_index ADD CONSTRAINT agent_index_acl_present CHECK (cardinality(allowed_principals) > 0);

-- Roles are cluster-wide: create them once, grant them to the migrating user so it can SET ROLE.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'konmari_agent') THEN CREATE ROLE konmari_agent NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'konmari_retention') THEN CREATE ROLE konmari_retention NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'konmari_keyholder') THEN CREATE ROLE konmari_keyholder NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'konmari_app') THEN CREATE ROLE konmari_app NOLOGIN; END IF;
    EXECUTE format('GRANT konmari_agent, konmari_retention, konmari_keyholder, konmari_app TO %I', current_user);
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'konmari roles not created (insufficient privilege); see docs/govern-guide.en.md';
END;
$$;

-- Agent identities read the governed index only, through RLS keyed on the transaction-scoped principal context.
GRANT SELECT ON agent_index TO konmari_agent;
ALTER TABLE agent_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_index FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_read ON agent_index FOR SELECT TO konmari_agent USING (
    allowed_principals && string_to_array(coalesce(current_setting('app.principals', true), ''), ',')
    AND NOT (denied_principals && string_to_array(coalesce(current_setting('app.principals', true), ''), ','))
    AND category_id = ANY (string_to_array(coalesce(current_setting('app.categories', true), ''), ','))
);
CREATE POLICY pipeline_all ON agent_index FOR ALL TO CURRENT_USER USING (true) WITH CHECK (true);

-- The pseudonym key: its own schema and role; no agent or app access.
REVOKE ALL ON SCHEMA konmari_keys FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA konmari_keys FROM PUBLIC, konmari_agent, konmari_app;
GRANT USAGE ON SCHEMA konmari_keys TO konmari_keyholder;
GRANT SELECT, INSERT ON konmari_keys.pseudonym_key TO konmari_keyholder;

-- audit_event becomes a monthly range-partitioned table; rows are copied unchanged (same id, hash and seq).
DROP VIEW decision_event;
ALTER TABLE audit_event RENAME TO audit_event_unpartitioned;
CREATE TABLE audit_event (
    id              TEXT NOT NULL,
    ts              TIMESTAMP NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    charter_id      TEXT,
    charter_version INTEGER,
    step            TEXT,
    result          TEXT,
    details         TEXT,
    seq             BIGINT,
    prev_hash       TEXT,
    hash            TEXT,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
CREATE TABLE audit_event_default PARTITION OF audit_event DEFAULT;
DO $$
DECLARE m date;
BEGIN
    FOR m IN SELECT DISTINCT date_trunc('month', ts)::date FROM audit_event_unpartitioned UNION
             SELECT date_trunc('month', now() AT TIME ZONE 'UTC')::date LOOP
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF audit_event FOR VALUES FROM (%L) TO (%L)',
                       'audit_event_' || to_char(m, 'YYYY_MM'), m, (m + interval '1 month')::date);
    END LOOP;
END;
$$;
INSERT INTO audit_event SELECT id, ts, actor, action, charter_id, charter_version, step, result, details, seq,
    prev_hash, hash FROM audit_event_unpartitioned;
DROP TABLE audit_event_unpartitioned;
CREATE INDEX audit_event_seq ON audit_event (seq);
CREATE VIEW decision_event AS SELECT * FROM audit_event WHERE step = 'discard';

-- Append-only for everyone except the retention role, which may only DELETE (purge with checkpoint).
CREATE OR REPLACE FUNCTION konmari_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_user = 'konmari_retention' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_event is append-only';
END;
$$;
CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION konmari_audit_append_only();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION konmari_audit_append_only();
GRANT SELECT, INSERT ON audit_event, decision_event TO konmari_app;
GRANT SELECT, DELETE ON audit_event TO konmari_retention;
