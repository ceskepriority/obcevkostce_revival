"""Check the aggregation rules against ČSÚ's own SO ORP figures.

For every dataset that also publishes SO ORP values, municipal values from
the database are combined per SO ORP with the indicator's declared rule and
compared with what ČSÚ publishes for that SO ORP:
  SUM            must match exactly
  WEIGHTED_MEAN  must match within a small tolerance (ČSÚ rounds its inputs)
A mismatch means the rule (or its weight) is wrong for that indicator, or that
some municipal values are missing (confidential) — both worth knowing.

ČSÚ keeps historical SO ORP figures in the boundaries valid at the time, while
we group municipalities by today's boundaries, so older years differ wherever a
municipality has moved between two SO ORP. Results are therefore also given
for recent periods only (`RECENT_FROM`), which is what judges the rule.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..db import copy_rows
from .parse import Release, Stats

REL_TOL = 0.002   # 0.2 % of the value
ABS_TOL = 0.006   # or about half a unit of the second decimal
RECENT_FROM = "2023-01-01"  # after the last transfer of municipalities between SO ORP
# Breakdown keys that describe the event, not a sub-population: a weight is
# looked up under the same value of these.
CONTEXT_KEYS = {"druh voleb", "kolo voleb", "typ zastupitelstva"}


def check(conn, directories: list[Path], log=print) -> dict:
    results: dict = {}
    for directory in directories:
        rel = Release(directory)
        if not rel.selection.get("orp") or not any((directory / "orp").glob("*.csv")):
            continue
        res = _check_release(conn, rel)
        results[rel.code] = res
        for code, r in res.items():
            judged = "recent" if r["recent_compared"] else "all"
            n, m = (r["recent_compared"], r["recent_matching"]) if r["recent_compared"] else (r["compared"], r["matching"])
            r["verdict"] = ("not aggregable" if r["rule"] == "NONE" else "no data to compare" if n == 0
                            else "ok" if m == n else "mostly" if m >= 0.95 * n else "MISMATCH")
            log(f"{r['verdict']:>18}  {rel.code} {code} {r['rule']}: {judged} periods {m}/{n}, "
                f"all periods {r['matching']}/{r['compared']}"
                + (f", worst recent difference {r['recent_worst']}" if r["recent_worst"] is not None else "")
                + (f" ({r['not_comparable']} not comparable)" if r["not_comparable"] else ""))
    return results


def _check_release(conn, rel: Release) -> dict:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("""CREATE TEMP TABLE orp_raw (series_id text, group_id text,
                                                  period daterange, value numeric) ON COMMIT DROP""")
        copy_rows(cur, "orp_raw", ["series_id", "group_id", "period", "value"],
                  ((s, g, f"[{lo},{hi})", v) for s, g, lo, hi, v in
                   rel.observations(folder="orp", territory=rel.selection["orp"],
                                    level="ORP", stats=Stats())))
        cur.execute("SELECT indicator_id, aggregation_method::text, weight_indicator_id FROM indicator")
        rules = {i: (agg, w) for i, agg, w in cur.fetchall()}
        cur.execute("SELECT series_key, indicator_id, breakdown FROM series")
        by_content = {(ind, json.dumps(b, sort_keys=True)): sid for sid, ind, b in cur.fetchall()}

        cur.execute("""CREATE TEMP TABLE smap (local text PRIMARY KEY, series_key integer, indicator_id text,
                                               rule text, weight_series_key integer) ON COMMIT DROP""")
        for sid, s in rel.series.items():
            agg, weight = rules.get(s.indicator_id, (None, None))
            db_id = by_content.get((s.indicator_id, json.dumps(s.breakdown, sort_keys=True)))
            weight_series = None
            if agg == "WEIGHTED_MEAN" and set(s.breakdown) <= CONTEXT_KEYS:
                weight_series = by_content.get((weight, json.dumps(s.breakdown, sort_keys=True)))
            cur.execute("INSERT INTO smap VALUES (%s, %s, %s, %s, %s)",
                        (sid, db_id, s.indicator_id, agg, weight_series))
        cur.execute("""CREATE TEMP TABLE orp ON COMMIT DROP AS
                       SELECT DISTINCT m.series_key, m.indicator_id, m.rule, m.weight_series_key,
                              r.group_id, r.period, r.value
                       FROM orp_raw r JOIN smap m ON m.local = r.series_id
                       WHERE m.series_key IS NOT NULL""")
        cur.execute("ANALYZE orp")
        cur.execute(
            """
            WITH wanted AS (SELECT DISTINCT series_key, weight_series_key, rule FROM orp),
            agg AS (
                SELECT w.series_key, g.group_id, c.period,
                       CASE w.rule
                            WHEN 'SUM' THEN sum(c.value)
                            WHEN 'WEIGHTED_MEAN' THEN sum(c.value * wt.value) / nullif(sum(wt.value), 0)
                       END AS value
                FROM wanted w
                JOIN current_observation c ON c.series_key = w.series_key
                JOIN municipality_group_member g ON g.municipality_id = c.municipality_id
                JOIN municipality_group mg ON mg.group_id = g.group_id AND mg.group_type = 'SO ORP'
                LEFT JOIN current_observation wt
                       ON wt.series_key = w.weight_series_key
                      AND wt.municipality_id = c.municipality_id AND wt.period = c.period
                WHERE w.rule IN ('SUM', 'WEIGHTED_MEAN')
                GROUP BY w.series_key, w.rule, g.group_id, c.period
            ),
            cmp AS (
                SELECT o.indicator_id, o.rule, o.series_key, o.group_id, o.period::text AS period,
                       lower(o.period) >= %s::date AS recent,
                       o.value AS published, a.value AS computed,
                       CASE WHEN a.value IS NULL THEN NULL
                            WHEN o.rule = 'SUM' THEN a.value = o.value
                            ELSE abs(a.value - o.value) <= greatest(%s, %s * abs(o.value)) END AS match
                FROM orp o LEFT JOIN agg a USING (series_key, group_id, period)
            )
            SELECT indicator_id, rule,
                   count(*) FILTER (WHERE match IS NOT NULL) AS compared,
                   count(*) FILTER (WHERE match) AS matching,
                   count(*) FILTER (WHERE match IS NULL) AS not_comparable,
                   count(*) FILTER (WHERE match IS NOT NULL AND recent) AS recent_compared,
                   count(*) FILTER (WHERE match AND recent) AS recent_matching,
                   max(abs(computed - published)) FILTER (WHERE NOT match AND recent) AS recent_worst,
                   (array_agg(json_build_object('series', series_key, 'orp', group_id, 'period', period,
                                                'published', published, 'computed', round(computed, 6))
                              ORDER BY recent DESC, abs(computed - published) DESC NULLS LAST)
                        FILTER (WHERE NOT match))[1:3] AS examples
            FROM cmp GROUP BY 1, 2 ORDER BY 1""", (RECENT_FROM, ABS_TOL, REL_TOL))
        out = {}
        for (ind, rule, compared, matching, not_comparable, recent_compared, recent_matching,
             recent_worst, examples) in cur.fetchall():
            out[ind] = {"rule": rule, "compared": compared, "matching": matching,
                        "not_comparable": not_comparable,
                        "recent_compared": recent_compared, "recent_matching": recent_matching,
                        "recent_worst": float(recent_worst) if recent_worst is not None else None,
                        "examples": examples or []}
        return out
