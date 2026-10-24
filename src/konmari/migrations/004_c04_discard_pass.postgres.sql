-- Postgres-only: the application role may read and insert decision events, never update or delete them.
-- The role is created when the migrating user may do so; otherwise grant it by hand (docs/discard-guide.en.md).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'konmari_app') THEN
        CREATE ROLE konmari_app NOLOGIN;
    END IF;
    GRANT SELECT, INSERT ON audit_event, decision_event TO konmari_app;
    REVOKE UPDATE, DELETE, TRUNCATE ON audit_event, decision_event FROM konmari_app;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'konmari_app role not created (insufficient privilege); grant SELECT, INSERT on audit_event by hand';
END;
$$;
