-- Observations: append-only fact table. Only admins (superusers or members of
-- the role 'ovk_admin', if it exists) may UPDATE, DELETE or TRUNCATE.

-- +goose Up
CREATE TABLE observation (
    series_id       TEXT      NOT NULL REFERENCES series (series_id),
    municipality_id TEXT      NOT NULL REFERENCES municipality (municipality_id),
    period          DATERANGE NOT NULL CHECK (NOT isempty(period)),
    value           NUMERIC   NOT NULL,
    release_id      BIGINT    NOT NULL REFERENCES release (release_id),
    PRIMARY KEY (series_id, municipality_id, period, release_id)
);

CREATE INDEX observation_municipality_idx ON observation (municipality_id);
CREATE INDEX observation_release_idx ON observation (release_id);

REVOKE UPDATE, DELETE, TRUNCATE ON observation FROM PUBLIC;

-- +goose StatementBegin
CREATE FUNCTION is_ovk_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper
    ) OR EXISTS (
        SELECT 1 FROM pg_roles r
        WHERE r.rolname = 'ovk_admin'
          AND pg_has_role(current_user, r.oid, 'MEMBER')
    );
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE FUNCTION observation_guard_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT is_ovk_admin() THEN
        RAISE EXCEPTION 'observation is append-only: % is not allowed for role %',
            TG_OP, current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
-- +goose StatementEnd

CREATE TRIGGER observation_no_update_delete
    BEFORE UPDATE OR DELETE ON observation
    FOR EACH ROW EXECUTE FUNCTION observation_guard_immutable();

CREATE TRIGGER observation_no_truncate
    BEFORE TRUNCATE ON observation
    FOR EACH STATEMENT EXECUTE FUNCTION observation_guard_immutable();

-- +goose Down
DROP TABLE observation;
DROP FUNCTION observation_guard_immutable();
DROP FUNCTION is_ovk_admin();
