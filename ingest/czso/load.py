"""Load a downloaded release into the database.

One transaction per release: the release row and its observations appear
together or not at all. Observations are append-only; a later release adds
only values that are new or changed.
"""

from __future__ import annotations

import json
from pathlib import Path

from psycopg.types.json import Jsonb

from ..db import copy_rows
from .download import load_datasets
from .geography import OKRES_SUFFIX
from .parse import Release, Stats, to_local_date

SOURCE = ("czso", "Český statistický úřad", "https://csu.gov.cz")
LOADING_CODE = "ingest/czso/load.py"
UNKNOWN_TIER = 99


def stage(cur, rel: Release, stats: Stats) -> int:
    """Parse the release's CSV files into temp table `stage` (deduplicated)."""
    cur.execute("""CREATE TEMP TABLE stage_raw (
                       series_id text, municipality_id integer, period daterange, value numeric
                   ) ON COMMIT DROP""")
    copy_rows(cur, "stage_raw", ["series_id", "municipality_id", "period", "value"],
              ((s, m, f"[{lo},{hi})", v) for s, m, lo, hi, v in rel.observations(stats=stats)))
    # The same cell can arrive twice (overlapping time variants); it must agree.
    cur.execute("""SELECT series_id, municipality_id, period::text, array_agg(DISTINCT value)
                   FROM stage_raw GROUP BY 1, 2, 3 HAVING count(DISTINCT value) > 1 LIMIT 5""")
    clashes = cur.fetchall()
    if clashes:
        raise ValueError(f"{rel.code}: contradictory duplicate cells, e.g. {clashes}")
    cur.execute("""CREATE TEMP TABLE stage ON COMMIT DROP AS
                   SELECT DISTINCT series_id, municipality_id, period, value FROM stage_raw""")
    cur.execute("DROP TABLE stage_raw")
    cur.execute("SELECT count(*) FROM stage")
    return cur.fetchone()[0]


def resolve_series(cur, rel: Release) -> None:
    """Create the release's series, or map them to existing ones.

    A series is identified by (indicator, breakdown). If ČSÚ renames a label,
    the series id (built from codes) still matches and the breakdown is updated.
    """
    cur.execute("SELECT DISTINCT series_id FROM stage")
    used = [r[0] for r in cur.fetchall()]
    indicators = sorted({rel.series[s].indicator_id for s in used})
    cur.execute("SELECT indicator_id FROM indicator WHERE indicator_id = ANY(%s)", (indicators,))
    known = {r[0] for r in cur.fetchall()}
    if set(indicators) - known:
        raise SystemExit(f"{rel.code}: indicators missing in the database "
                         f"({sorted(set(indicators) - known)[:5]}); run `czso indicators`.")
    cur.execute("SELECT series_id, indicator_id, breakdown FROM series WHERE indicator_id = ANY(%s)",
                (indicators,))
    by_id, by_content = {}, {}
    for sid, ind, breakdown in cur.fetchall():
        by_id[sid] = breakdown
        by_content[(ind, json.dumps(breakdown, sort_keys=True))] = sid
    remap = []
    for sid in used:
        s = rel.series[sid]
        content = (s.indicator_id, json.dumps(s.breakdown, sort_keys=True))
        if content in by_content:
            if by_content[content] != sid:
                remap.append((by_content[content], sid))
        elif sid in by_id:
            cur.execute("UPDATE series SET breakdown = %s WHERE series_id = %s",
                        (Jsonb(s.breakdown), sid))
        else:
            cur.execute("INSERT INTO series (series_id, indicator_id, breakdown) VALUES (%s, %s, %s)",
                        (sid, s.indicator_id, Jsonb(s.breakdown)))
    # Observations carry the compact series key, not the text id.
    cur.execute("CREATE TEMP TABLE local_series (local text PRIMARY KEY, series_id text) ON COMMIT DROP")
    renamed = dict((local, db) for db, local in remap)
    cur.executemany("INSERT INTO local_series VALUES (%s, %s)",
                    [(sid, renamed.get(sid, sid)) for sid in used])
    cur.execute("""CREATE TEMP TABLE stage_keyed ON COMMIT DROP AS
                   SELECT se.series_key, st.municipality_id, st.period, st.value
                   FROM stage st
                   JOIN local_series ls ON ls.local = st.series_id
                   JOIN series se ON se.series_id = ls.series_id""")
    cur.execute("SELECT (SELECT count(*) FROM stage), (SELECT count(*) FROM stage_keyed)")
    before, after = cur.fetchone()
    if before != after:
        raise RuntimeError(f"{rel.code}: {before - after} staged cells lost their series")
    cur.execute("DROP TABLE stage")
    cur.execute("ALTER TABLE stage_keyed RENAME TO stage")
    cur.execute("CREATE INDEX ON stage (series_key, municipality_id, period)")
    cur.execute("ANALYZE stage")


def ensure_municipalities(cur, rel: Release) -> int:
    names = rel.municipalities()
    cur.execute("""SELECT DISTINCT s.municipality_id FROM stage s
                   LEFT JOIN municipality m USING (municipality_id)
                   WHERE m.municipality_id IS NULL""")
    missing = [r[0] for r in cur.fetchall()]
    cur.executemany(
        "INSERT INTO municipality (municipality_id, municipality_name) VALUES (%s, %s)",
        [(code, OKRES_SUFFIX.sub("", names[str(code)]).strip()) for code in missing])
    return len(missing)


def load_release(conn, directory: Path, code_version: str, log=print) -> dict | None:
    rel = Release(directory)
    cfg = load_datasets()
    tiers = {code: cfg["tiers"][d["tier"]] for code, d in cfg["datasets"].items()}
    my_tier = tiers[rel.code]
    raw_uri = directory.as_uri()
    stats = Stats()

    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT release_id FROM release WHERE dataset_id = %s AND raw_uri = %s",
                    (rel.code, raw_uri))
        if cur.fetchone():
            log(f"{rel.code}: this release is already loaded")
            return None

        cur.execute("""INSERT INTO source (source_id, source_name, homepage_url) VALUES (%s, %s, %s)
                       ON CONFLICT (source_id) DO NOTHING""", SOURCE)
        cur.execute("""INSERT INTO dataset (dataset_id, source_id, dataset_name) VALUES (%s, %s, %s)
                       ON CONFLICT (dataset_id) DO UPDATE SET dataset_name = EXCLUDED.dataset_name""",
                    (rel.code, SOURCE[0], rel.sada["nazev"].strip()))

        staged = stage(cur, rel, stats)
        new_municipalities = ensure_municipalities(cur, rel)
        resolve_series(cur, rel)

        cur.execute("SELECT max(release_id) FROM release WHERE dataset_id = %s", (rel.code,))
        previous = cur.fetchone()[0]
        fetched = max(f["fetched_at"] for f in rel.manifest["files"].values())
        source_url = ((rel.sada.get("metadataNKOD") or {}).get("dokumentaceURI")
                      or f"https://data.csu.gov.cz/datastat/info/SADA/{rel.code}")
        cur.execute(
            """INSERT INTO release (dataset_id, label, published_at, retrieved_at, source_url,
                                    raw_uri, loading_code, code_version, supersedes, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING release_id""",
            (rel.code, f"v{rel.sada['verze']} · import {rel.meta['idPripravenyImport']}",
             to_local_date(rel.meta["casZverejneni"]), to_local_date(fetched), source_url,
             raw_uri, LOADING_CODE, code_version, previous,
             json.dumps({**stats.as_dict(), "distinct_cells": staged}, ensure_ascii=False)))
        release_id = cur.fetchone()[0]

        # What is current for the staged cells, and which dataset says so.
        cur.execute("CREATE TEMP TABLE tier (dataset_id text PRIMARY KEY, tier int) ON COMMIT DROP")
        cur.executemany("INSERT INTO tier VALUES (%s, %s)", list(tiers.items()))
        cur.execute(
            """CREATE TEMP TABLE cur_obs ON COMMIT DROP AS
               SELECT DISTINCT ON (o.series_key, o.municipality_id, o.period)
                      o.series_key, o.municipality_id, o.period, o.value,
                      r.dataset_id, coalesce(t.tier, %s) AS tier
               FROM observation o
               JOIN release r USING (release_id)
               LEFT JOIN tier t USING (dataset_id)
               WHERE o.series_key IN (SELECT DISTINCT series_key FROM stage)
               ORDER BY o.series_key, o.municipality_id, o.period,
                        r.published_at DESC, r.release_id DESC""", (UNKNOWN_TIER,))
        cur.execute("CREATE INDEX ON cur_obs (series_key, municipality_id, period)")
        cur.execute("ANALYZE cur_obs")
        cur.execute(
            """CREATE TEMP TABLE decision ON COMMIT DROP AS
               SELECT s.*,
                      CASE WHEN c.series_key IS NULL THEN 'new'
                           WHEN c.tier < %(t)s THEN 'skipped_higher_tier_has_it'
                           WHEN c.tier > %(t)s THEN 'replaces_lower_tier'
                           WHEN c.value = s.value THEN 'unchanged'
                           WHEN c.dataset_id = %(d)s THEN 'revised'
                           ELSE 'differs_from_same_tier_dataset' END AS what
               FROM stage s LEFT JOIN cur_obs c USING (series_key, municipality_id, period)""",
            {"t": my_tier, "d": rel.code})
        cur.execute(
            """INSERT INTO observation (series_key, municipality_id, period, value, release_id)
               SELECT series_key, municipality_id, period, value, %s FROM decision
               WHERE what IN ('new', 'replaces_lower_tier', 'revised',
                              'differs_from_same_tier_dataset')""", (release_id,))
        inserted = cur.rowcount
        cur.execute("SELECT what, count(*) FROM decision GROUP BY 1")
        decisions = dict(cur.fetchall())

        report = {
            "dataset": rel.code, "release_id": release_id, "raw_uri": raw_uri,
            "code_version": code_version, **stats.as_dict(),
            "distinct_cells": staged, "inserted": inserted, "decisions": decisions,
            "series": len(rel.series), "municipalities_added": new_municipalities,
        }
        if stats.loaded - staged:
            report["duplicate_cells_dropped"] = stats.loaded - staged
    log(f"{rel.code}: release {release_id}: {stats.rows} file rows -> {staged} municipality cells, "
        f"{inserted} inserted {decisions}; empty {stats.empty}, other levels {stats.non_municipality}")
    return report
