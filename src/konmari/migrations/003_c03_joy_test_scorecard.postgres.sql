-- Postgres-only hardening: published rubric versions and the review history refuse UPDATE and DELETE.
CREATE OR REPLACE FUNCTION konmari_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER rubric_version_immutable
    BEFORE UPDATE OR DELETE ON rubric_version
    FOR EACH ROW EXECUTE FUNCTION konmari_append_only();

CREATE TRIGGER verdict_review_append_only
    BEFORE UPDATE OR DELETE ON verdict_review
    FOR EACH ROW EXECUTE FUNCTION konmari_append_only();
