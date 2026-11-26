-- Comparison groups for the dashboards.
--
--   * Size bands (population at 31. 12. of the latest year) become ordinary
--     municipality groups of type 'velikost'; their members are recomputed on
--     every refresh.
--   * aggregate_observation() combines municipal values into group values by
--     each indicator's aggregation rule. It is the one place that does so: the
--     preset groups (materialised view group_observation) and custom sets
--     (computed on demand) both go through it, so they agree exactly.
--   * comparison_set stores custom sets (groups and/or individual obce) under a
--     short id derived from their normalised definition.
--
-- refresh_current_observation() now refreshes everything derived from the
-- observations, in order: current_observation, size bands, group_observation.

-- +goose Up

-- ---------------------------------------------------------------- size bands
CREATE TABLE size_band (
    group_id    TEXT PRIMARY KEY REFERENCES municipality_group (group_id) ON DELETE CASCADE,
    lower_bound INTEGER NOT NULL,          -- inclusive
    upper_bound INTEGER,                   -- exclusive; NULL = no upper bound
    CHECK (upper_bound IS NULL OR upper_bound > lower_bound)
);

INSERT INTO municipality_group (group_id, group_name, group_type) VALUES
    ('velikost_0_500',         'Obce do 499 obyvatel',              'velikost'),
    ('velikost_500_1000',      'Obce 500–999 obyvatel',             'velikost'),
    ('velikost_1000_2000',     'Obce 1 000–1 999 obyvatel',         'velikost'),
    ('velikost_2000_5000',     'Obce 2 000–4 999 obyvatel',         'velikost'),
    ('velikost_5000_10000',    'Obce 5 000–9 999 obyvatel',         'velikost'),
    ('velikost_10000_20000',   'Obce 10 000–19 999 obyvatel',       'velikost'),
    ('velikost_20000_50000',   'Obce 20 000–49 999 obyvatel',       'velikost'),
    ('velikost_50000_100000',  'Obce 50 000–99 999 obyvatel',       'velikost'),
    ('velikost_100000_',       'Obce od 100 000 obyvatel',          'velikost');

INSERT INTO size_band (group_id, lower_bound, upper_bound) VALUES
    ('velikost_0_500',         0,      500),
    ('velikost_500_1000',      500,    1000),
    ('velikost_1000_2000',     1000,   2000),
    ('velikost_2000_5000',     2000,   5000),
    ('velikost_5000_10000',    5000,   10000),
    ('velikost_10000_20000',   10000,  20000),
    ('velikost_20000_50000',   20000,  50000),
    ('velikost_50000_100000',  50000,  100000),
    ('velikost_100000_',       100000, NULL);

-- Members of each band: population (czso.2406K) at 31. 12. of the latest year
-- that has it. Obce without a value for that year belong to no band.
-- +goose StatementBegin
CREATE FUNCTION refresh_size_bands() RETURNS void
LANGUAGE sql
SET search_path = public, pg_temp AS $$
    DELETE FROM municipality_group_member WHERE group_id IN (SELECT group_id FROM size_band);

    WITH pop AS (
        SELECT c.municipality_id, c.value, lower(c.period) AS start
        FROM current_observation c JOIN series s ON s.series_key = c.series_key
        WHERE s.series_id = 'czso.2406K'
    )
    INSERT INTO municipality_group_member (group_id, municipality_id)
    SELECT b.group_id, p.municipality_id
    FROM pop p
    JOIN size_band b ON p.value >= b.lower_bound AND (b.upper_bound IS NULL OR p.value < b.upper_bound)
    WHERE p.start = (SELECT max(start) FROM pop);
$$;
-- +goose StatementEnd
REVOKE ALL ON FUNCTION refresh_size_bands() FROM PUBLIC;

-- ---------------------------------------------------------------- aggregation
-- The weight of a WEIGHTED_MEAN series: the weight indicator's series with the
-- same breakdown, but only where the breakdown describes the event (elections),
-- not a sub-population. Same rule as CONTEXT_KEYS in ingest/czso/aggregation.py
-- (which validates the rules against ČSÚ's SO ORP figures) - keep them in step.
CREATE VIEW series_weight AS
SELECT s.series_key, w.series_key AS weight_series_key
FROM series s
JOIN indicator i ON i.indicator_id = s.indicator_id
JOIN series w ON w.indicator_id = i.weight_indicator_id AND w.breakdown = s.breakdown
WHERE i.aggregation_method = 'WEIGHTED_MEAN'
  AND NOT EXISTS (SELECT 1 FROM jsonb_object_keys(s.breakdown) k
                  WHERE k NOT IN ('druh voleb', 'kolo voleb', 'typ zastupitelstva'));

-- Group values from municipal values. The groups are given as parallel arrays
-- of (group key, member obec), so many groups are computed in one pass.
--   SUM            sum of the members' values
--   MEAN           plain mean
--   WEIGHTED_MEAN  sum(value * weight) / sum(weight) over members having both
--   NONE           not aggregable: no rows
-- n_values is the number of members that contributed; compare it with the
-- group's size to spot incomplete figures (confidential or missing values).
-- p_series limits the result to those series (NULL = all).
-- +goose StatementBegin
CREATE FUNCTION aggregate_observation(p_group text[], p_member integer[], p_series integer[] DEFAULT NULL)
RETURNS TABLE (group_key text, series_key integer, period daterange, value numeric, n_values integer)
LANGUAGE sql STABLE
SET search_path = public, pg_temp AS $$
    WITH m AS (
        SELECT DISTINCT u.g, u.mem FROM unnest(p_group, p_member) AS u(g, mem)
    ),
    s AS (
        SELECT se.series_key, i.aggregation_method AS method, sw.weight_series_key
        FROM series se
        JOIN indicator i ON i.indicator_id = se.indicator_id
        LEFT JOIN series_weight sw ON sw.series_key = se.series_key
        WHERE i.aggregation_method <> 'NONE'
          AND (p_series IS NULL OR se.series_key = ANY (p_series))
    ),
    obs AS (
        SELECT m.g, s.series_key, s.method, s.weight_series_key, c.municipality_id, c.period, c.value
        FROM s
        JOIN current_observation c ON c.series_key = s.series_key
        JOIN m ON m.mem = c.municipality_id
    )
    SELECT o.g, o.series_key, o.period,
           CASE o.method WHEN 'SUM' THEN sum(o.value) ELSE avg(o.value) END,
           count(*)::integer
    FROM obs o
    WHERE o.method IN ('SUM', 'MEAN')
    GROUP BY o.g, o.series_key, o.method, o.period
    UNION ALL
    -- weighted means apart: joining the weights for every row would scan all observations
    SELECT o.g, o.series_key, o.period,
           sum(o.value * wt.value) / nullif(sum(wt.value), 0),
           count(*)::integer
    FROM obs o
    JOIN current_observation wt
      ON wt.series_key = o.weight_series_key
     AND wt.municipality_id = o.municipality_id
     AND wt.period = o.period
    WHERE o.method = 'WEIGHTED_MEAN'
    GROUP BY o.g, o.series_key, o.period;
$$;
-- +goose StatementEnd

-- One custom set of obce.
-- +goose StatementBegin
CREATE FUNCTION aggregate_members(p_member integer[], p_series integer[] DEFAULT NULL)
RETURNS TABLE (series_key integer, period daterange, value numeric, n_values integer)
LANGUAGE sql STABLE
SET search_path = public, pg_temp AS $$
    SELECT a.series_key, a.period, a.value, a.n_values
    FROM aggregate_observation(array_fill(''::text, ARRAY[cardinality(p_member)]), p_member, p_series) a;
$$;
-- +goose StatementEnd

-- ---------------------------------------------------------------- preset groups
-- Every group in municipality_group_member (stát, kraj, okres, SO ORP, size bands).
-- Built empty here and filled at the end of the migration.
CREATE MATERIALIZED VIEW group_observation AS
SELECT a.group_key AS group_id, a.series_key, a.period, a.value, a.n_values
FROM (SELECT array_agg(group_id ORDER BY group_id, municipality_id) AS g,
             array_agg(municipality_id ORDER BY group_id, municipality_id) AS m
      FROM municipality_group_member) x
CROSS JOIN LATERAL aggregate_observation(x.g, x.m) a
WITH NO DATA;

-- Unique index is required for REFRESH ... CONCURRENTLY.
CREATE UNIQUE INDEX group_observation_pk ON group_observation (group_id, series_key, period);

-- On the big disk where it exists (see 00008).
-- +goose StatementBegin
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tablespace WHERE spcname = 'ovk_big') THEN
        ALTER MATERIALIZED VIEW group_observation SET TABLESPACE ovk_big;
        ALTER INDEX group_observation_pk SET TABLESPACE ovk_big;
    END IF;
END;
$$;
-- +goose StatementEnd

-- ---------------------------------------------------------------- refresh
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION refresh_current_observation(p_concurrent boolean DEFAULT true)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
    IF p_concurrent THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.current_observation;
    ELSE
        REFRESH MATERIALIZED VIEW public.current_observation;
    END IF;
    PERFORM public.refresh_size_bands();
    IF p_concurrent THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.group_observation;
    ELSE
        REFRESH MATERIALIZED VIEW public.group_observation;
    END IF;
END;
$$;
-- +goose StatementEnd
COMMENT ON FUNCTION refresh_current_observation(boolean) IS
    'Refresh everything derived from observations and group membership: '
    'current_observation, size band members, group_observation.';

-- ---------------------------------------------------------------- custom sets
CREATE TABLE comparison_set (
    set_id     TEXT PRIMARY KEY,           -- see comparison_set_save()
    definition JSONB NOT NULL,             -- {"groups": [group_id, ...], "obce": [municipality_id, ...]}
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Store a custom set and return its id. The definition is normalised (sorted,
-- without duplicates), so the same set always gets the same id: the first 16
-- hex digits of the SHA-256 of the normalised definition.
-- +goose StatementBegin
CREATE FUNCTION comparison_set_save(p_definition jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
    v_groups text[];
    v_obce   integer[];
    v_bad    text;
    v_def    jsonb;
    v_id     text;
BEGIN
    IF jsonb_typeof(p_definition) IS DISTINCT FROM 'object'
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_definition) k WHERE k NOT IN ('groups', 'obce'))
       OR jsonb_typeof(coalesce(p_definition -> 'groups', '[]')) <> 'array'
       OR jsonb_typeof(coalesce(p_definition -> 'obce', '[]')) <> 'array' THEN
        RAISE EXCEPTION 'comparison set must be {"groups": [...], "obce": [...]}, got %', p_definition
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_groups := ARRAY(SELECT DISTINCT x FROM jsonb_array_elements_text(coalesce(p_definition -> 'groups', '[]')) x
                      ORDER BY 1);
    v_obce := ARRAY(SELECT DISTINCT x::integer FROM jsonb_array_elements_text(coalesce(p_definition -> 'obce', '[]')) x
                    ORDER BY 1);
    IF cardinality(v_groups) + cardinality(v_obce) = 0 THEN
        RAISE EXCEPTION 'comparison set is empty' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT string_agg(g, ', ') INTO v_bad
    FROM unnest(v_groups) g WHERE NOT EXISTS (SELECT 1 FROM municipality_group WHERE group_id = g);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'unknown groups: %', v_bad USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT string_agg(o::text, ', ') INTO v_bad
    FROM unnest(v_obce) o WHERE NOT EXISTS (SELECT 1 FROM municipality WHERE municipality_id = o);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'unknown obce: %', v_bad USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_def := jsonb_build_object('groups', to_jsonb(v_groups), 'obce', to_jsonb(v_obce));
    v_id := left(encode(sha256(convert_to(v_def::text, 'UTF8')), 'hex'), 16);
    INSERT INTO comparison_set (set_id, definition) VALUES (v_id, v_def)
    ON CONFLICT (set_id) DO NOTHING;
    RETURN v_id;
END;
$$;
-- +goose StatementEnd
REVOKE ALL ON FUNCTION comparison_set_save(jsonb) FROM PUBLIC;

-- The obce of a custom set, with group membership as it is now (size bands
-- follow the latest population). Unknown id = no rows.
-- +goose StatementBegin
CREATE FUNCTION comparison_set_members(p_set_id text) RETURNS SETOF integer
LANGUAGE sql STABLE
SET search_path = public, pg_temp AS $$
    SELECT m.municipality_id
    FROM comparison_set c
    CROSS JOIN LATERAL jsonb_array_elements_text(c.definition -> 'groups') g
    JOIN municipality_group_member m ON m.group_id = g
    WHERE c.set_id = p_set_id
    UNION
    SELECT o::integer
    FROM comparison_set c
    CROSS JOIN LATERAL jsonb_array_elements_text(c.definition -> 'obce') o
    WHERE c.set_id = p_set_id;
$$;
-- +goose StatementEnd

-- ---------------------------------------------------------------- access
GRANT SELECT ON size_band, series_weight, group_observation, comparison_set TO ovk_writer;
GRANT EXECUTE ON FUNCTION comparison_set_save(jsonb) TO ovk_writer;

-- The web app's role, where it exists: read everything, write only through
-- comparison_set_save().
-- +goose StatementBegin
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ovk_web') THEN
        RAISE NOTICE 'role ovk_web not found; no grants for the web app';
        RETURN;
    END IF;
    GRANT SELECT ON
        source, dataset, release, indicator, series,
        municipality, municipality_group, municipality_group_member, size_band,
        current_observation, group_observation, series_weight, comparison_set
        TO ovk_web;
    GRANT EXECUTE ON FUNCTION comparison_set_save(jsonb) TO ovk_web;
END;
$$;
-- +goose StatementEnd

-- ---------------------------------------------------------------- fill
SELECT refresh_size_bands();
REFRESH MATERIALIZED VIEW group_observation;

-- +goose Down
-- +goose StatementBegin
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ovk_web') THEN
        REVOKE ALL ON
            source, dataset, release, indicator, series,
            municipality, municipality_group, municipality_group_member, size_band,
            current_observation, group_observation, series_weight, comparison_set
            FROM ovk_web;
        REVOKE ALL ON FUNCTION comparison_set_save(jsonb) FROM ovk_web;
    END IF;
END;
$$;
-- +goose StatementEnd

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION refresh_current_observation(p_concurrent boolean DEFAULT true)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
    IF p_concurrent THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.current_observation;
    ELSE
        REFRESH MATERIALIZED VIEW public.current_observation;
    END IF;
END;
$$;
-- +goose StatementEnd
COMMENT ON FUNCTION refresh_current_observation(boolean) IS NULL;

DROP FUNCTION comparison_set_members(text);
DROP FUNCTION comparison_set_save(jsonb);
DROP TABLE comparison_set;
DROP MATERIALIZED VIEW group_observation;
DROP FUNCTION aggregate_members(integer[], integer[]);
DROP FUNCTION aggregate_observation(text[], integer[], integer[]);
DROP VIEW series_weight;
DROP FUNCTION refresh_size_bands();
DELETE FROM municipality_group WHERE group_id IN (SELECT group_id FROM size_band);  -- members cascade
DROP TABLE size_band;
