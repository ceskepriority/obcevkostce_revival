-- Access for the ingestion role. Tables are owned by the migration role, so
-- ovk_writer needs explicit grants. It may append data and maintain metadata,
-- but never rewrite observations or releases.

-- +goose Up
GRANT SELECT, INSERT ON
    source, dataset, release, indicator, series,
    municipality, municipality_group, municipality_group_member, observation
    TO ovk_writer;

-- Metadata upserts (names, labels, aggregation rules, municipality types).
GRANT UPDATE ON source, dataset, indicator, series, municipality, municipality_group
    TO ovk_writer;

-- Membership is replaced wholesale when ČSÚ changes territorial structure.
GRANT DELETE ON municipality_group_member TO ovk_writer;

GRANT SELECT ON current_observation TO ovk_writer;

-- Only the owner can refresh a materialised view; expose it as a function.
-- +goose StatementBegin
CREATE FUNCTION refresh_current_observation(p_concurrent boolean DEFAULT true)
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

REVOKE ALL ON FUNCTION refresh_current_observation(boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION refresh_current_observation(boolean) TO ovk_writer;

-- +goose Down
DROP FUNCTION refresh_current_observation(boolean);
REVOKE ALL ON
    source, dataset, release, indicator, series,
    municipality, municipality_group, municipality_group_member, observation,
    current_observation
    FROM ovk_writer;
