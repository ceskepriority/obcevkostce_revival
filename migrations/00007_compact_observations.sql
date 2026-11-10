-- Shrink the observation tables (measured: 333 -> 226 bytes per value including
-- the current_observation view):
--   * observations refer to a series by a 4-byte key instead of its text id
--   * municipality ids (ČSÚ kód obce) and release ids become integers
--   * observation keeps only its primary key plus a tiny BRIN index on release;
--     reads go through current_observation, which has the lookup indexes
-- The observation tables are rebuilt, so this must run before data is loaded.

-- +goose Up
-- +goose StatementBegin
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM observation) THEN
        RAISE EXCEPTION 'observation is not empty; this migration rebuilds it';
    END IF;
END;
$$;
-- +goose StatementEnd

DROP MATERIALIZED VIEW current_observation;
DROP TABLE observation;

ALTER TABLE series ADD COLUMN series_key integer GENERATED ALWAYS AS IDENTITY UNIQUE;

ALTER TABLE municipality_group_member DROP CONSTRAINT municipality_group_member_municipality_id_fkey;
ALTER TABLE municipality DROP CONSTRAINT municipality_municipality_id_check;
ALTER TABLE municipality ALTER COLUMN municipality_id TYPE integer USING municipality_id::integer;
ALTER TABLE municipality ADD CONSTRAINT municipality_municipality_id_check
    CHECK (municipality_id BETWEEN 100000 AND 999999);  -- ČSÚ kód obce, six digits
ALTER TABLE municipality_group_member
    ALTER COLUMN municipality_id TYPE integer USING municipality_id::integer;
ALTER TABLE municipality_group_member ADD CONSTRAINT municipality_group_member_municipality_id_fkey
    FOREIGN KEY (municipality_id) REFERENCES municipality (municipality_id) ON DELETE CASCADE;

ALTER TABLE release DROP CONSTRAINT release_supersedes_fkey;
ALTER TABLE release ALTER COLUMN release_id TYPE integer;
ALTER TABLE release ALTER COLUMN supersedes TYPE integer;
ALTER TABLE release ADD CONSTRAINT release_supersedes_fkey
    FOREIGN KEY (supersedes) REFERENCES release (release_id);

CREATE TABLE observation (
    series_key      INTEGER   NOT NULL REFERENCES series (series_key),
    municipality_id INTEGER   NOT NULL REFERENCES municipality (municipality_id),
    period          DATERANGE NOT NULL CHECK (NOT isempty(period)),
    value           NUMERIC   NOT NULL,
    release_id      INTEGER   NOT NULL REFERENCES release (release_id),
    PRIMARY KEY (series_key, municipality_id, period, release_id)
);

-- Rows of a release are written together, so a block-range index is enough.
CREATE INDEX observation_release_brin ON observation USING brin (release_id);

REVOKE UPDATE, DELETE, TRUNCATE ON observation FROM PUBLIC;

CREATE TRIGGER observation_no_update_delete
    BEFORE UPDATE OR DELETE ON observation
    FOR EACH ROW EXECUTE FUNCTION observation_guard_immutable();

CREATE TRIGGER observation_no_truncate
    BEFORE TRUNCATE ON observation
    FOR EACH STATEMENT EXECUTE FUNCTION observation_guard_immutable();

CREATE MATERIALIZED VIEW current_observation AS
SELECT DISTINCT ON (o.series_key, o.municipality_id, o.period)
       o.series_key,
       o.municipality_id,
       o.period,
       o.value,
       o.release_id
FROM observation o
JOIN release r USING (release_id)
ORDER BY o.series_key, o.municipality_id, o.period,
         r.published_at DESC, r.release_id DESC;

CREATE UNIQUE INDEX current_observation_pk
    ON current_observation (series_key, municipality_id, period);
CREATE INDEX current_observation_municipality_idx
    ON current_observation (municipality_id);

GRANT SELECT, INSERT ON observation TO ovk_writer;
GRANT SELECT ON current_observation TO ovk_writer;

-- +goose Down
DROP MATERIALIZED VIEW current_observation;
DROP TABLE observation;

ALTER TABLE series DROP COLUMN series_key;

ALTER TABLE release DROP CONSTRAINT release_supersedes_fkey;
ALTER TABLE release ALTER COLUMN release_id TYPE bigint;
ALTER TABLE release ALTER COLUMN supersedes TYPE bigint;
ALTER TABLE release ADD CONSTRAINT release_supersedes_fkey
    FOREIGN KEY (supersedes) REFERENCES release (release_id);

ALTER TABLE municipality_group_member DROP CONSTRAINT municipality_group_member_municipality_id_fkey;
ALTER TABLE municipality DROP CONSTRAINT municipality_municipality_id_check;
ALTER TABLE municipality ALTER COLUMN municipality_id TYPE text USING municipality_id::text;
ALTER TABLE municipality ADD CONSTRAINT municipality_municipality_id_check
    CHECK (municipality_id ~ '^[0-9]{6}$');
ALTER TABLE municipality_group_member
    ALTER COLUMN municipality_id TYPE text USING municipality_id::text;
ALTER TABLE municipality_group_member ADD CONSTRAINT municipality_group_member_municipality_id_fkey
    FOREIGN KEY (municipality_id) REFERENCES municipality (municipality_id) ON DELETE CASCADE;

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
CREATE TRIGGER observation_no_update_delete
    BEFORE UPDATE OR DELETE ON observation
    FOR EACH ROW EXECUTE FUNCTION observation_guard_immutable();
CREATE TRIGGER observation_no_truncate
    BEFORE TRUNCATE ON observation
    FOR EACH STATEMENT EXECUTE FUNCTION observation_guard_immutable();

CREATE MATERIALIZED VIEW current_observation AS
SELECT DISTINCT ON (o.series_id, o.municipality_id, o.period)
       o.series_id, o.municipality_id, o.period, o.value, o.release_id
FROM observation o
JOIN release r USING (release_id)
ORDER BY o.series_id, o.municipality_id, o.period,
         r.published_at DESC, r.release_id DESC;
CREATE UNIQUE INDEX current_observation_pk
    ON current_observation (series_id, municipality_id, period);
CREATE INDEX current_observation_municipality_idx
    ON current_observation (municipality_id);

GRANT SELECT, INSERT ON observation TO ovk_writer;
GRANT SELECT ON current_observation TO ovk_writer;
