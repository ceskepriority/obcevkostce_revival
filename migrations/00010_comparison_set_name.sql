-- Custom comparison sets get an optional name, shown on the charts:
--   comparison_set_save('{"name": "Sousedé", "obce": [596230, 595209]}')
-- The name is part of the normalised definition and so of the id: the same
-- members under another name are another set. Sets without a name keep their ids.

-- +goose Up
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION comparison_set_save(p_definition jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
    v_name   text;
    v_groups text[];
    v_obce   integer[];
    v_bad    text;
    v_def    jsonb;
    v_id     text;
BEGIN
    IF jsonb_typeof(p_definition) IS DISTINCT FROM 'object'
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_definition) k WHERE k NOT IN ('name', 'groups', 'obce'))
       OR jsonb_typeof(coalesce(p_definition -> 'groups', '[]')) <> 'array'
       OR jsonb_typeof(coalesce(p_definition -> 'obce', '[]')) <> 'array'
       OR jsonb_typeof(coalesce(p_definition -> 'name', '""')) <> 'string' THEN
        RAISE EXCEPTION 'comparison set must be {"name": "...", "groups": [...], "obce": [...]}, got %', p_definition
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_name := nullif(btrim(p_definition ->> 'name'), '');
    IF p_definition ? 'name' AND v_name IS NULL THEN
        RAISE EXCEPTION 'comparison set name is empty' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF char_length(v_name) > 80 THEN
        RAISE EXCEPTION 'comparison set name is longer than 80 characters' USING ERRCODE = 'invalid_parameter_value';
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
    IF v_name IS NOT NULL THEN
        v_def := v_def || jsonb_build_object('name', v_name);
    END IF;
    v_id := left(encode(sha256(convert_to(v_def::text, 'UTF8')), 'hex'), 16);
    INSERT INTO comparison_set (set_id, definition) VALUES (v_id, v_def)
    ON CONFLICT (set_id) DO NOTHING;
    RETURN v_id;
END;
$$;
-- +goose StatementEnd

-- +goose Down
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION comparison_set_save(p_definition jsonb) RETURNS text
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
