"""Indicators of the configured datasets with their aggregation rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .download import latest_release_dir, load_datasets

HERE = Path(__file__).resolve().parent
AGGREGATIONS = {"SUM", "WEIGHTED_MEAN", "MEAN", "NONE"}


# YAML 1.1 reads 1_2 as the integer 12; such codes must be quoted.
UNQUOTED_UNDERSCORE = re.compile(r"(?<![\w\"'])\d+_\d+(?![\w\"'])")


def load_rules() -> dict[str, dict]:
    """{code: {agg, norm, weight}} with 'self' resolved."""
    rules: dict[str, dict] = {}
    text = (HERE / "indicators.yaml").read_text(encoding="utf-8")
    for n, line in enumerate(text.splitlines(), 1):
        code_part = line.split("#", 1)[0]
        if "note:" not in code_part and UNQUOTED_UNDERSCORE.search(code_part):
            raise ValueError(f"indicators.yaml:{n}: quote codes like {UNQUOTED_UNDERSCORE.search(code_part).group()}")
    doc = yaml.safe_load(text)
    for group in doc["rules"]:
        agg = group["agg"]
        if agg not in AGGREGATIONS:
            raise ValueError(f"unknown aggregation {agg!r}")
        if (agg == "WEIGHTED_MEAN") != ("weight" in group):
            raise ValueError(f"weight is required for, and only for, WEIGHTED_MEAN: {group}")
        for code in group["codes"]:
            if not isinstance(code, (str, int)) or isinstance(code, bool):
                raise ValueError(f"bad indicator code {code!r}")
            code = str(code)
            if code in rules:
                raise ValueError(f"indicator {code} has two rules")
            norm = group.get("norm")
            rules[code] = {
                "agg": agg,
                "norm": code if norm == "self" else (str(norm) if norm else None),
                "weight": str(group["weight"]) if "weight" in group else None,
            }
    return rules


def catalogue(root: Path, codes: list[str] | None = None) -> dict[str, dict]:
    """{indicator code: catalogue entry} from downloaded datasets, taken from
    the highest-priority dataset that publishes the indicator."""
    cfg = load_datasets()
    tiers = cfg["tiers"]
    datasets = cfg["datasets"]
    wanted = codes or list(datasets)
    out: dict[str, dict] = {}
    for code in sorted(wanted, key=lambda c: tiers[datasets[c]["tier"]]):
        directory = latest_release_dir(root, code)
        if directory is None:
            continue
        sada = json.loads((directory / "sada.json").read_text(encoding="utf-8"))
        for u in sada["ukazatele"]:
            out.setdefault(u["kod"], u)
    return out


def sync(conn, root: Path, log=print) -> None:
    """Upsert every indicator found in downloaded datasets."""
    rules = load_rules()
    found = catalogue(root)
    missing = sorted(set(found) - set(rules))
    if missing:
        raise SystemExit(f"No aggregation rule in indicators.yaml for: {', '.join(missing)}")
    rows = []
    for code, u in found.items():
        rule = rules[code]
        for ref in (rule["norm"], rule["weight"]):
            if ref and ref not in found:
                raise SystemExit(
                    f"Indicator {code} refers to {ref}, whose dataset is not downloaded yet.")
        unit = (u.get("mericiJednotka") or {}).get("nazev")
        rows.append((
            f"czso.{code}", u["nazev"].strip(), unit, (u.get("definiceNahled") or "").strip() or None,
            rule["norm"] is not None,
            f"czso.{rule['norm']}" if rule["norm"] else None,
            rule["agg"],
            f"czso.{rule['weight']}" if rule["weight"] else None,
        ))
    # One statement, so references between the rows resolve at its end.
    placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s, %s::aggregation_method, %s)"] * len(rows))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO indicator (indicator_id, indicator_name, unit_of_measure, definition,
                                       normalisable, normalisation_indicator_id,
                                       aggregation_method, weight_indicator_id)
                VALUES {placeholders}
                ON CONFLICT (indicator_id) DO UPDATE SET
                    indicator_name = EXCLUDED.indicator_name,
                    unit_of_measure = EXCLUDED.unit_of_measure,
                    definition = EXCLUDED.definition,
                    normalisable = EXCLUDED.normalisable,
                    normalisation_indicator_id = EXCLUDED.normalisation_indicator_id,
                    aggregation_method = EXCLUDED.aggregation_method,
                    weight_indicator_id = EXCLUDED.weight_indicator_id""",
            [v for row in rows for v in row],
        )
    log(f"indicators: {len(rows)} upserted")
