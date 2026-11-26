package obce

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Members of a group or custom set, sorted by name.
func Members(ctx context.Context, db *pgxpool.Pool, dir *Directory, it Item) ([]Obec, error) {
	var sql string
	switch it.Kind {
	case 'g':
		sql = `SELECT municipality_id FROM municipality_group_member WHERE group_id = $1`
	case 's':
		sql = `SELECT comparison_set_members($1)`
	default:
		return nil, fmt.Errorf("%w: %s has no members", ErrSelection, it.Token())
	}
	rows, err := db.Query(ctx, sql, it.ID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Obec
	for rows.Next() {
		var id int
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		if o, ok := dir.Get(id); ok {
			out = append(out, o)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].fold != out[j].fold {
			return out[i].fold < out[j].fold
		}
		return out[i].ORP < out[j].ORP
	})
	return out, rows.Err()
}

// Indicator with what the export says about it.
type Indicator struct {
	ID, Name, Unit, Method string
	WeightName             string // WEIGHTED_MEAN: name of the weight indicator
	Source                 string // "dataset name (ID), zveřejněno …", from the obec's own values
}

// Series of an indicator under one breakdown.
type Series struct {
	Key       int
	Indicator string
	Breakdown map[string]string
}

// Value of one series in one period; Start/End are the period's first and last day.
type Value struct {
	Series     int
	Start, End time.Time
	Value      float64
}

func Indicators(ctx context.Context, db *pgxpool.Pool, obec int) ([]Indicator, error) {
	rows, err := db.Query(ctx, `
		WITH src AS (  -- the newest release of the obec's values of each indicator
			SELECT DISTINCT ON (s.indicator_id) s.indicator_id,
			       format('%s (%s), zveřejněno %s', d.dataset_name, d.dataset_id, to_char(r.published_at, 'DD. MM. YYYY')) AS source
			FROM current_observation c
			JOIN series s USING (series_key)
			JOIN release r ON r.release_id = c.release_id
			JOIN dataset d ON d.dataset_id = r.dataset_id
			WHERE c.municipality_id = $1
			ORDER BY s.indicator_id, r.published_at DESC
		)
		SELECT i.indicator_id, i.indicator_name, coalesce(i.unit_of_measure, ''), i.aggregation_method::text,
		       coalesce(w.indicator_name, ''), coalesce(src.source, '')
		FROM indicator i
		LEFT JOIN indicator w ON w.indicator_id = i.weight_indicator_id
		LEFT JOIN src ON src.indicator_id = i.indicator_id
		WHERE EXISTS (SELECT 1 FROM series s WHERE s.indicator_id = i.indicator_id)
		ORDER BY i.indicator_id`, obec)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Indicator
	for rows.Next() {
		var i Indicator
		if err := rows.Scan(&i.ID, &i.Name, &i.Unit, &i.Method, &i.WeightName, &i.Source); err != nil {
			return nil, err
		}
		out = append(out, i)
	}
	return out, rows.Err()
}

func AllSeries(ctx context.Context, db *pgxpool.Pool) ([]Series, error) {
	rows, err := db.Query(ctx, `SELECT series_key, indicator_id, breakdown FROM series`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Series
	for rows.Next() {
		var s Series
		var br map[string]any
		if err := rows.Scan(&s.Key, &s.Indicator, &br); err != nil {
			return nil, err
		}
		s.Breakdown = make(map[string]string, len(br))
		for k, v := range br {
			s.Breakdown[k] = fmt.Sprint(v)
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// ObecValues: every value of one obec.
func ObecValues(ctx context.Context, db *pgxpool.Pool, id int) ([]Value, error) {
	return queryValues(ctx, db, `
		SELECT series_key, lower(period), upper(period) - 1, value::float8
		FROM current_observation WHERE municipality_id = $1`, id)
}

// ItemValues: every value of a comparison item, aggregated by each indicator's
// rule (indicators with rule NONE have no group values).
func ItemValues(ctx context.Context, db *pgxpool.Pool, it Item) ([]Value, error) {
	switch it.Kind {
	case 'g':
		return queryValues(ctx, db, `
			SELECT series_key, lower(period), upper(period) - 1, value::float8
			FROM group_observation WHERE group_id = $1`, it.ID)
	case 'o':
		id, _ := strconv.Atoi(it.ID)
		return ObecValues(ctx, db, id)
	case 's':
		return queryValues(ctx, db, `
			SELECT series_key, lower(period), upper(period) - 1, value::float8
			FROM aggregate_members(ARRAY(SELECT comparison_set_members($1)))`, it.ID)
	}
	return nil, fmt.Errorf("unknown item kind %q", it.Kind)
}

func queryValues(ctx context.Context, db *pgxpool.Pool, sql string, args ...any) ([]Value, error) {
	rows, err := db.Query(ctx, sql, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Value
	for rows.Next() {
		var v Value
		if err := rows.Scan(&v.Series, &v.Start, &v.End, &v.Value); err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
