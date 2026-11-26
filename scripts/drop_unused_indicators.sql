-- Keep only the indicators we use or plan to use, with all of their series; drop
-- every other indicator, its series and its observations. Land use is kept for
-- its newest year only.
-- Run as ovk_admin / superuser (observation is append-only for other roles):
--   psql -v ON_ERROR_STOP=1 -d obcevkostce_revival -f scripts/drop_unused_indicators.sql

\timing on
BEGIN;

-- latest_only: keep only the newest period of the indicator
CREATE TEMP TABLE keep_indicator (indicator_id text PRIMARY KEY, latest_only boolean NOT NULL DEFAULT false)
    ON COMMIT DROP;

INSERT INTO keep_indicator (indicator_id) VALUES
    -- demo charts (demo/build.py)
    ('czso.2406K'),        -- obyvatelé k 31. 12.
    ('czso.2406K2'),       -- obyvatelé k 31. 12. podle pohlaví a věkových skupin
    ('czso.2406P'),        -- obyvatelé k 1. 1. podle pohlaví a věkových skupin
    ('czso.9379W'),        -- střední stav (1. 7.)
    ('czso.5401'),         -- přirozený přírůstek
    ('czso.5402'),         -- saldo migrace
    ('czso.5402W'),        -- saldo migrace podle věku
    -- population movement
    ('czso.4355ZN'),       -- živě narození
    ('czso.5393'),         -- zemřelí
    ('czso.5403'),         -- přistěhovalí (i podle věku)
    ('czso.5404'),         -- vystěhovalí (i podle věku)
    ('czso.5436'),         -- průměrný věk
    -- unemployment (REGUAP)
    ('czso.5973UAP961'), ('czso.5973UAP962'), ('czso.5973UAP963'),
    ('czso.5973UAP964'), ('czso.5973UAP965'),
    -- completed dwellings (REGUAP)
    ('czso.3103UAP991'), ('czso.3113UAP992'),
    -- normalisation of the two groups above
    ('czso.2406UAP891'),   -- obyvatelé k 31. 12. (housing)
    ('czso.2406UAP905');   -- obyvatelé 15–64 let k 31. 12. (unemployment)

-- land use and the coefficient of ecological stability: newest year only
INSERT INTO keep_indicator (indicator_id, latest_only)
SELECT indicator_id, true FROM indicator
WHERE indicator_id LIKE 'czso.4582UAP%' OR indicator_id = 'czso.5594UAP11320';

-- every kept indicator must exist, and must not refer to a dropped one
DO $$
DECLARE missing text; dangling text;
BEGIN
    SELECT string_agg(k.indicator_id, ', ') INTO missing
    FROM keep_indicator k LEFT JOIN indicator i USING (indicator_id) WHERE i.indicator_id IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'kept indicators missing: %', missing;
    END IF;
    SELECT string_agg(i.indicator_id, ', ') INTO dangling
    FROM indicator i JOIN keep_indicator USING (indicator_id)
    WHERE i.normalisation_indicator_id NOT IN (SELECT indicator_id FROM keep_indicator)
       OR i.weight_indicator_id NOT IN (SELECT indicator_id FROM keep_indicator);
    IF dangling IS NOT NULL THEN
        RAISE EXCEPTION 'kept indicators refer to dropped ones: %', dangling;
    END IF;
END $$;

-- copy kept observations aside (on the big disk), then rebuild the table
SET LOCAL temp_tablespaces = 'ovk_big';
CREATE TEMP TABLE keep_observation ON COMMIT DROP AS
WITH latest AS (  -- newest period start of each latest_only indicator
    SELECT s.indicator_id, max(lower(o.period)) AS start
    FROM observation o JOIN series s USING (series_key)
    JOIN keep_indicator k USING (indicator_id)
    WHERE k.latest_only
    GROUP BY 1
)
SELECT o.* FROM observation o
JOIN series s USING (series_key)
JOIN keep_indicator k USING (indicator_id)
LEFT JOIN latest l USING (indicator_id)
WHERE NOT k.latest_only OR lower(o.period) = l.start;

TRUNCATE observation;  -- the guard trigger lets admins through
INSERT INTO observation SELECT * FROM keep_observation;

DELETE FROM series WHERE indicator_id NOT IN (SELECT indicator_id FROM keep_indicator);
DELETE FROM indicator WHERE indicator_id NOT IN (SELECT indicator_id FROM keep_indicator);

SELECT s.indicator_id, count(*) AS observations,
       min(lower(o.period)) AS first_period, max(lower(o.period)) AS last_period
FROM observation o JOIN series s USING (series_key)
GROUP BY 1 ORDER BY 1;

REFRESH MATERIALIZED VIEW current_observation;
COMMIT;

ANALYZE observation;
ANALYZE current_observation;
ANALYZE series;
