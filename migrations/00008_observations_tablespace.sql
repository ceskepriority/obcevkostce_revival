-- Keep the two large objects (observation, current_observation) and their
-- indexes in the tablespace "ovk_big" (an external disk), so they do not fill
-- the system disk. The ingestion role may also put its temporary staging
-- tables there. Where the tablespace does not exist this does nothing.

-- +goose Up
-- +goose StatementBegin
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_tablespace WHERE spcname = 'ovk_big') THEN
        RAISE NOTICE 'tablespace ovk_big not found; leaving objects where they are';
        RETURN;
    END IF;
    ALTER TABLE observation SET TABLESPACE ovk_big;
    ALTER INDEX observation_pkey SET TABLESPACE ovk_big;
    ALTER INDEX observation_release_brin SET TABLESPACE ovk_big;
    ALTER MATERIALIZED VIEW current_observation SET TABLESPACE ovk_big;
    ALTER INDEX current_observation_pk SET TABLESPACE ovk_big;
    ALTER INDEX current_observation_municipality_idx SET TABLESPACE ovk_big;
    GRANT CREATE ON TABLESPACE ovk_big TO ovk_writer;
END;
$$;
-- +goose StatementEnd

-- +goose Down
-- +goose StatementBegin
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_tablespace WHERE spcname = 'ovk_big') THEN
        RETURN;
    END IF;
    REVOKE CREATE ON TABLESPACE ovk_big FROM ovk_writer;
    ALTER INDEX current_observation_municipality_idx SET TABLESPACE pg_default;
    ALTER INDEX current_observation_pk SET TABLESPACE pg_default;
    ALTER MATERIALIZED VIEW current_observation SET TABLESPACE pg_default;
    ALTER INDEX observation_release_brin SET TABLESPACE pg_default;
    ALTER INDEX observation_pkey SET TABLESPACE pg_default;
    ALTER TABLE observation SET TABLESPACE pg_default;
END;
$$;
-- +goose StatementEnd
