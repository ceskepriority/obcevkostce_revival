-- Latest value per (series, municipality, period): the observation from the
-- most recently published release; ties broken by the newer release_id.
-- Refresh after each load with:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY current_observation;

-- +goose Up
CREATE MATERIALIZED VIEW current_observation AS
SELECT DISTINCT ON (o.series_id, o.municipality_id, o.period)
       o.series_id,
       o.municipality_id,
       o.period,
       o.value,
       o.release_id
FROM observation o
JOIN release r USING (release_id)
ORDER BY o.series_id, o.municipality_id, o.period,
         r.published_at DESC, r.release_id DESC;

-- Unique index is required for REFRESH ... CONCURRENTLY.
CREATE UNIQUE INDEX current_observation_pk
    ON current_observation (series_id, municipality_id, period);
CREATE INDEX current_observation_municipality_idx
    ON current_observation (municipality_id);

-- +goose Down
DROP MATERIALIZED VIEW current_observation;
