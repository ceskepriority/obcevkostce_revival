-- Municipalities and their groupings (ORP, okres, kraj, associations, ...).

-- +goose Up
-- Ordered: obec < městys < město < statutární město < krajské město < hlavní město
CREATE TYPE municipality_type AS ENUM (
    'obec', 'mestys', 'mesto', 'statutarni_mesto', 'krajske_mesto', 'hlavni_mesto'
);

CREATE TABLE municipality (
    municipality_id   TEXT PRIMARY KEY
        CHECK (municipality_id ~ '^[0-9]{6}$'),  -- ČSÚ kód obce
    municipality_name TEXT NOT NULL,
    municipality_type municipality_type
);

CREATE TABLE municipality_group (
    group_id        TEXT PRIMARY KEY,            -- mirrors ČSÚ code where available
    group_name      TEXT NOT NULL,
    group_type      TEXT,                        -- e.g. 'ORP', 'okres', 'kraj', 'DSO'
    parent_group_id TEXT REFERENCES municipality_group (group_id), -- ORP <- okres <- kraj
    CHECK (parent_group_id IS DISTINCT FROM group_id)
);

CREATE INDEX municipality_group_parent_idx ON municipality_group (parent_group_id);
CREATE INDEX municipality_group_type_idx ON municipality_group (group_type);

CREATE TABLE municipality_group_member (
    group_id        TEXT NOT NULL REFERENCES municipality_group (group_id) ON DELETE CASCADE,
    municipality_id TEXT NOT NULL REFERENCES municipality (municipality_id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, municipality_id)
);

CREATE INDEX municipality_group_member_municipality_idx
    ON municipality_group_member (municipality_id);

-- +goose Down
DROP TABLE municipality_group_member;
DROP TABLE municipality_group;
DROP TABLE municipality;
DROP TYPE municipality_type;
