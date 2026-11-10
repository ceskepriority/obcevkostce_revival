-- Indicators (what is measured) and series (an indicator under a specific breakdown).

-- +goose Up
CREATE TYPE aggregation_method AS ENUM ('SUM', 'WEIGHTED_MEAN', 'MEAN', 'NONE');

CREATE TABLE indicator (
    indicator_id               TEXT PRIMARY KEY,
    indicator_name             TEXT NOT NULL,
    unit_of_measure            TEXT,
    definition                 TEXT,
    normalisable               BOOLEAN NOT NULL DEFAULT false, -- can it be computed e.g. per capita?
    normalisation_indicator_id TEXT REFERENCES indicator (indicator_id), -- e.g. population size
    aggregation_method         aggregation_method NOT NULL,
    weight_indicator_id        TEXT REFERENCES indicator (indicator_id), -- for WEIGHTED_MEAN
    CHECK (normalisation_indicator_id IS NULL OR normalisable),
    CHECK (aggregation_method <> 'WEIGHTED_MEAN' OR weight_indicator_id IS NOT NULL)
);

CREATE TABLE series (
    series_id    TEXT PRIMARY KEY,
    indicator_id TEXT NOT NULL REFERENCES indicator (indicator_id),
    breakdown    JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(breakdown) = 'object'),  -- e.g. {"sex":"F","age":"65+"}
    UNIQUE (indicator_id, breakdown)
);

CREATE INDEX series_breakdown_idx ON series USING gin (breakdown jsonb_path_ops);

-- +goose Down
DROP TABLE series;
DROP TABLE indicator;
DROP TYPE aggregation_method;
