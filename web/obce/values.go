package obce

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"obcevkostce/web/dash"
)

// Annual values only: periods of one calendar year.
const annual = `upper(o.period) - lower(o.period) BETWEEN 365 AND 366`

// ObecRows: the values of one obec.
func ObecRows(ctx context.Context, db *pgxpool.Pool, id int, indicators []string) ([]dash.Row, error) {
	return queryRows(ctx, db, `
		SELECT s.indicator_id, s.breakdown, extract(year FROM lower(o.period))::int, o.value::float8
		FROM current_observation o JOIN series s USING (series_key)
		WHERE o.municipality_id = $1 AND s.indicator_id = ANY($2) AND `+annual, id, indicators)
}

// ItemRows: the values of one comparison item, aggregated by each indicator's rule.
// Preset groups are precomputed (group_observation); custom sets are aggregated now.
func ItemRows(ctx context.Context, db *pgxpool.Pool, it Item, indicators []string) ([]dash.Row, error) {
	switch it.Kind {
	case 'g':
		return queryRows(ctx, db, `
			SELECT s.indicator_id, s.breakdown, extract(year FROM lower(o.period))::int, o.value::float8
			FROM group_observation o JOIN series s USING (series_key)
			WHERE o.group_id = $1 AND s.indicator_id = ANY($2) AND `+annual, it.ID, indicators)
	case 'o':
		id, _ := strconv.Atoi(it.ID)
		return ObecRows(ctx, db, id, indicators)
	case 's':
		return queryRows(ctx, db, `
			SELECT s.indicator_id, s.breakdown, extract(year FROM lower(o.period))::int, o.value::float8
			FROM aggregate_members(
			         ARRAY(SELECT comparison_set_members($1)),
			         ARRAY(SELECT series_key FROM series WHERE indicator_id = ANY($2))) o
			JOIN series s USING (series_key)
			WHERE `+annual, it.ID, indicators)
	}
	return nil, fmt.Errorf("unknown item kind %q", it.Kind)
}

func queryRows(ctx context.Context, db *pgxpool.Pool, sql string, args ...any) ([]dash.Row, error) {
	rows, err := db.Query(ctx, sql, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []dash.Row
	for rows.Next() {
		var r dash.Row
		var br map[string]any
		if err := rows.Scan(&r.Indicator, &br, &r.Year, &r.Value); err != nil {
			return nil, err
		}
		r.Breakdown = make(map[string]string, len(br))
		for k, v := range br {
			r.Breakdown[k] = fmt.Sprint(v)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// Sources: the ČSÚ datasets as chart sources, with their latest publication date.
func Sources(ctx context.Context, db *pgxpool.Pool, datasets []string) (map[string]dash.Source, error) {
	rows, err := db.Query(ctx, `
		SELECT d.dataset_id, d.dataset_name, max(r.published_at)
		FROM dataset d JOIN release r USING (dataset_id)
		WHERE d.dataset_id = ANY($1) GROUP BY 1, 2`, datasets)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]dash.Source{}
	for rows.Next() {
		var id, name string
		var published time.Time
		if err := rows.Scan(&id, &name, &published); err != nil {
			return nil, err
		}
		out[id] = dash.Source{Name: "ČSÚ",
			Dataset: fmt.Sprintf("%s (%s, zveřejněno %s)", name, id, published.Format("02. 01. 2006")),
			URL:     "https://data.csu.gov.cz/datastat/info/SADA/" + id}
	}
	return out, rows.Err()
}
