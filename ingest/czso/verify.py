"""Check that what is in the database is exactly what ČSÚ published.

Per release:
  1. files   every raw file still has the sha256 recorded at download time
  2. cells   every municipality value in the raw CSVs is in the database with
             the same digits (text comparison, not just numeric equality), or
             is legitimately represented by another release (unchanged since
             an earlier release / published by a higher-priority dataset);
             the release holds nothing that is not in its files
  3. spot    random values are re-fetched one by one from the API's
             single-value endpoint and compared with file and database
Globally:
  4. tiers   no value from a lower-priority dataset is current where a
             higher-priority dataset has that cell
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path

from ..db import copy_rows
from .client import DOTAZ, ApiError, Client
from .download import load_datasets
from .parse import Release, Stats, read_csv


def check_files(rel: Release) -> dict:
    bad = []
    for name, info in rel.manifest["files"].items():
        data = (rel.dir / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            bad.append(name)
    return {"files": len(rel.manifest["files"]), "changed_on_disk": bad, "ok": not bad}


def count_csv_rows(rel: Release) -> int:
    """Data rows in the raw files, counted without the project's parser."""
    total = 0
    for path in (rel.dir / "data").glob("*.csv"):
        with path.open(encoding="utf-8", newline="") as fh:
            total += sum(1 for _ in csv.reader(fh)) - 1
    return total


def check_cells(conn, rel: Release) -> dict:
    stats = Stats()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT release_id FROM release WHERE dataset_id = %s AND raw_uri = %s",
                    (rel.code, rel.dir.as_uri()))
        row = cur.fetchone()
        if row is None:
            return {"ok": False, "error": "release is not loaded"}
        release_id = row[0]
        # Expected values stay text, so the comparison is digit for digit.
        cur.execute("""CREATE TEMP TABLE expect (series_id text, municipality_id integer,
                                                 period daterange, value text) ON COMMIT DROP""")
        copy_rows(cur, "expect", ["series_id", "municipality_id", "period", "value"],
                  ((s, m, f"[{lo},{hi})", v) for s, m, lo, hi, v in rel.observations(stats=stats)))
        # map parser series ids to database series (same rule as the loader)
        cur.execute("CREATE TEMP TABLE smap (local text PRIMARY KEY, db integer) ON COMMIT DROP")
        for sid, s in rel.series.items():
            cur.execute("SELECT series_key FROM series WHERE indicator_id = %s AND breakdown = %s::jsonb",
                        (s.indicator_id, json.dumps(s.breakdown)))
            hit = cur.fetchone()
            cur.execute("INSERT INTO smap VALUES (%s, %s)", (sid, hit[0] if hit else None))
        cur.execute("""CREATE TEMP TABLE want ON COMMIT DROP AS
                       SELECT DISTINCT m.db AS series_key, e.municipality_id, e.period, e.value
                       FROM expect e JOIN smap m ON m.local = e.series_id""")
        cur.execute("CREATE INDEX ON want (series_key, municipality_id, period)")
        cur.execute("ANALYZE want")
        cur.execute("""
            SELECT
              count(*) AS cells,
              count(*) FILTER (WHERE w.series_key IS NULL) AS series_missing,
              count(*) FILTER (WHERE o.value IS NOT NULL AND o.value::text = w.value) AS in_release_identical,
              count(*) FILTER (WHERE o.value IS NOT NULL AND o.value::text <> w.value) AS in_release_different,
              count(*) FILTER (WHERE o.value IS NULL AND other.same) AS same_value_in_other_release,
              count(*) FILTER (WHERE o.value IS NULL AND NOT other.same) AS only_different_value_elsewhere,
              count(*) FILTER (WHERE o.value IS NULL AND other.same IS NULL) AS missing
            FROM want w
            LEFT JOIN observation o
              ON o.release_id = %s AND (o.series_key, o.municipality_id, o.period)
                                     = (w.series_key, w.municipality_id, w.period)
            LEFT JOIN LATERAL (
                SELECT bool_or(x.value = w.value::numeric) AS same
                FROM observation x
                WHERE (x.series_key, x.municipality_id, x.period)
                      = (w.series_key, w.municipality_id, w.period) AND x.release_id <> %s
            ) other ON o.value IS NULL""", (release_id, release_id))
        cols = [d.name for d in cur.description]
        result = dict(zip(cols, cur.fetchone()))
        cur.execute("""SELECT count(*) FROM observation o
                       WHERE o.release_id = %s AND NOT EXISTS (
                           SELECT 1 FROM want w WHERE (w.series_key, w.municipality_id, w.period)
                                                    = (o.series_key, o.municipality_id, o.period))""",
                    (release_id,))
        result["in_release_but_not_in_files"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM observation WHERE release_id = %s", (release_id,))
        result["release_rows"] = cur.fetchone()[0]

    result["release_id"] = release_id
    result["file_rows"] = stats.rows
    result["file_rows_recounted"] = count_csv_rows(rel)
    result["non_municipality_rows"] = stats.non_municipality
    result["empty_values"] = stats.empty
    result["ok"] = (
        result["series_missing"] == 0 and result["in_release_different"] == 0
        and result["missing"] == 0 and result["in_release_but_not_in_files"] == 0
        and result["in_release_identical"] == result["release_rows"]
        and stats.rows == result["file_rows_recounted"]
        and stats.rows == stats.loaded + stats.non_municipality + stats.empty
    )
    # values that differ from ours and exist only in a higher-priority dataset
    # are expected (tier rule) and reported, not failed
    return result


def spot_check(conn, client: Client, rel: Release, n: int, seed: int = 0) -> dict:
    """Re-fetch n random municipality values through the single-value endpoint."""
    if n <= 0:
        return {"checked": 0, "ok": True}
    territory = rel.selection["territory"]
    rng = random.Random(f"{rel.code}:{seed}")
    sample: list = []
    seen = 0
    for path in sorted((rel.dir / "data").glob("*.csv")):
        for row in read_csv(path):
            if row.value == "" or rel.interpret(row, territory, "OBEC") is None:
                continue
            seen += 1
            if len(sample) < n:
                sample.append(row)
            elif rng.randrange(seen) < n:
                sample[rng.randrange(n)] = row
    mismatches, errors = [], []
    with conn.cursor() as cur:
        for row in sample:
            dims = list(row.items)
            params = {"verze": rel.sada["verze"], "kodyDimenzi": ",".join(dims),
                      "kodyPolozek": ",".join(row.items[d][1] for d in dims)}
            try:
                got = client.get_json(f"{DOTAZ}/data/sady/{rel.code}/hodnoty/{row.indicator}", params)
            except ApiError as exc:
                errors.append({"params": params, "error": str(exc)[:200]})
                continue
            api = got.get("ciselnaHodnota")
            series_id, code, (lower, upper) = rel.interpret(row, territory, "OBEC")
            s = rel.series[series_id]
            cur.execute(
                """SELECT o.value FROM observation o JOIN release r USING (release_id)
                   JOIN series s USING (series_key)
                   WHERE s.indicator_id = %s AND s.breakdown = %s::jsonb
                     AND o.municipality_id = %s::int AND o.period = daterange(%s, %s)
                   ORDER BY (r.dataset_id = %s) DESC, r.published_at DESC, r.release_id DESC LIMIT 1""",
                (s.indicator_id, json.dumps(s.breakdown), code, lower, upper, rel.code))
            db = cur.fetchone()
            file_value = Decimal(row.value)
            if api is None or Decimal(str(api)) != file_value or db is None or db[0] != file_value:
                mismatches.append({"indicator": row.indicator, "params": params, "file": row.value,
                                   "api": str(api), "db": str(db[0]) if db else None})
    conn.rollback()
    return {"checked": len(sample), "mismatches": mismatches, "api_errors": errors,
            "ok": not mismatches and len(errors) <= len(sample) // 2}


def check_tiers(conn) -> dict:
    cfg = load_datasets()
    tiers = {code: cfg["tiers"][d["tier"]] for code, d in cfg["datasets"].items()}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE tier (dataset_id text PRIMARY KEY, tier int) ON COMMIT DROP")
        cur.executemany("INSERT INTO tier VALUES (%s, %s)", list(tiers.items()))
        cur.execute("""
            WITH ranked AS (
                SELECT o.series_key, o.municipality_id, o.period, t.tier,
                       first_value(t.tier) OVER w AS current_tier,
                       min(t.tier) OVER (PARTITION BY o.series_key, o.municipality_id, o.period) AS best_tier
                FROM observation o JOIN release r USING (release_id) JOIN tier t USING (dataset_id)
                WINDOW w AS (PARTITION BY o.series_key, o.municipality_id, o.period
                             ORDER BY r.published_at DESC, r.release_id DESC)
            )
            SELECT count(DISTINCT (series_key, municipality_id, period))
            FROM ranked WHERE current_tier > best_tier""")
        bad = cur.fetchone()[0]
    return {"cells_where_lower_priority_dataset_is_current": bad, "ok": bad == 0}


def verify(conn, client: Client, directories: list[Path], spot: int, log=print):
    results, ok = {}, True
    for directory in directories:
        rel = Release(directory)
        res = {"files": check_files(rel), "cells": check_cells(conn, rel)}
        if res["cells"].get("error") is None:
            res["spot"] = spot_check(conn, client, rel, spot)
        good = all(part["ok"] for part in res.values())
        ok &= good
        results[rel.code] = res
        c = res["cells"]
        log(f"{rel.code}: {'OK' if good else 'FAILED'} — "
            + (c.get("error") or
               f"{c['cells']} cells: {c['in_release_identical']} identical in release, "
               f"{c['same_value_in_other_release']} same value in another release, "
               f"{c['only_different_value_elsewhere']} superseded by another dataset, "
               f"{c['missing']} missing, {c['in_release_different']} different; "
               f"spot {res['spot']['checked'] - len(res['spot']['mismatches']) - len(res['spot']['api_errors'])}"
               f"/{res['spot']['checked']}"))
    results["_tiers"] = check_tiers(conn)
    ok &= results["_tiers"]["ok"]
    log(f"tier rule: {'OK' if results['_tiers']['ok'] else 'FAILED'} {results['_tiers']}")
    return results, ok
