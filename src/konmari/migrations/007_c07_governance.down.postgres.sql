-- Runs before 007_c07_governance.down.sql: GIN indexes go with their columns; audit_event becomes one table again.
DROP VIEW decision_event;
ALTER TABLE audit_event RENAME TO audit_event_partitioned;
CREATE TABLE audit_event (
    id TEXT PRIMARY KEY, ts TIMESTAMP NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, charter_id TEXT,
    charter_version INTEGER, step TEXT, result TEXT, details TEXT, seq BIGINT, prev_hash TEXT, hash TEXT
);
INSERT INTO audit_event SELECT * FROM audit_event_partitioned;
DROP TABLE audit_event_partitioned;
CREATE VIEW decision_event AS SELECT * FROM audit_event WHERE step = 'discard';
CREATE OR REPLACE FUNCTION konmari_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only';
END;
$$;
CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION konmari_audit_append_only();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION konmari_audit_append_only();
GRANT SELECT, INSERT ON audit_event, decision_event TO konmari_app;
