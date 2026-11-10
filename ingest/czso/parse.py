"""Parse downloaded DataStat CSV files into observation records.

Nothing is computed here: a value travels as the exact string ČSÚ printed
(validated as a plain decimal) and PostgreSQL parses it into NUMERIC.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

PRAGUE = ZoneInfo("Europe/Prague")
DECIMAL = re.compile(r"-?\d+(\.\d+)?")
# columns that may follow the dimensions; SPOLEHLIVOST* (reliability flag) is optional
KNOWN_TAIL = {"Hodnota", "MJ_TEXT", "MJ_SYMBOL", "POZNAMKA", "POZNAMKA_TEXT",
              "SPOLEHLIVOST", "SPOLEHLIVOST_TEXT"}

# Readable breakdown keys per ČSÚ dimension type; other types fall back to the
# (lower-cased) name of the dimension variant.
BREAKDOWN_KEYS = {
    "POHLAVI": "pohlaví",
    "VEK": "věk",
    "VEKSKUP": "věk",
    "KAT": "kategorie ubytovacího zařízení",
    "UZEMIZ": "země pobytu hosta",
    "FORMA": "právní forma",
    "CZNACE": "odvětví (CZ-NACE)",
    "KATP": "počet zaměstnanců",
    "ISEKTOR": "institucionální sektor",
    "TYPPLOCH": "typ plochy",
    "DRUHSOCZAR": "druh zařízení sociálních služeb",
    "UKONVZDEL": "vzdělání",
    "RODSTAV": "rodinný stav",
    "NARODN": "národnost",
    "STOBCAN": "státní občanství",
    "EKONAKTIV": "ekonomická aktivita",
    "POSTZAM": "postavení v zaměstnání",
    "BYDRPS": "bydliště rok před sčítáním",
    "DETICE": "počet živě narozených dětí",
    "ZPBYDLENI": "způsob bydlení",
    "MTBOBV": "registrovaný pobyt",
    "ZAMEST": "třída zaměstnání",
    "DRUHVOLEB": "druh voleb",
    "VSTRANA": "volební strana",
    "VOLKOLO": "kolo voleb",
    "KANDIDAT": "kandidát",
    "TYPZASTUP": "typ zastupitelstva",
}


def to_local_date(ts: str) -> date:
    """'2023-12-31T23:00:00Z' -> 2024-01-01 (ČSÚ stores Prague midnights in UTC)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PRAGUE).date()


def period_of(item: dict) -> tuple[date, date]:
    """Half-open [lower, upper) date range of a time codelist item."""
    if item.get("casOd") and item.get("casDo"):
        lower, upper = to_local_date(item["casOd"]), to_local_date(item["casDo"])
        if upper == lower:  # a reference day, e.g. the census
            upper = lower + timedelta(days=1)
        if upper < lower:
            raise ValueError(f"time item {item['kod']}: upper before lower")
        return lower, upper
    if re.fullmatch(r"(19|20)\d\d", item["kod"]):  # undated items, e.g. election years
        year = int(item["kod"])
        return date(year, 1, 1), date(year + 1, 1, 1)
    raise ValueError(f"cannot derive a period from time item {item!r}")


@dataclass(frozen=True)
class RawRow:
    indicator: str
    items: dict[str, tuple[str | None, str]]  # variant code -> (level, item code)
    value: str
    note: str
    reliability: str = ""


def read_csv(path: Path) -> Iterator[RawRow]:
    """Rows of one DataStat CSV (format=CSV&kodZvlast=true).

    Columns: Ukazatel, IndicatorType, then per dimension a label column and a
    code column named `VARIANT` or, for hierarchies, `VARIANT.LEVEL` (one pair
    per level, top first; a row's item is its deepest filled level).
    """
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        if header[:2] != ["Ukazatel", "IndicatorType"] or "Hodnota" not in header:
            raise ValueError(f"{path}: unexpected header {header}")
        i_value = header.index("Hodnota")
        tail = header[i_value:]
        if not set(tail) <= KNOWN_TAIL or "POZNAMKA" not in tail:
            raise ValueError(f"{path}: unexpected columns after the value: {tail}")
        i_note = header.index("POZNAMKA")
        i_flag = header.index("SPOLEHLIVOST") if "SPOLEHLIVOST" in tail else None
        dim_cols = header[2:i_value]
        if len(dim_cols) % 2:
            raise ValueError(f"{path}: dimension columns are not label/code pairs")
        code_cols: list[tuple[int, str, str | None]] = []
        for i in range(1, len(dim_cols), 2):
            variant, _, level = dim_cols[i].partition(".")
            code_cols.append((2 + i, variant, level or None))
        n = len(header)
        for row in reader:
            if len(row) != n:
                raise ValueError(f"{path}:{reader.line_num}: {len(row)} fields, expected {n}")
            items: dict[str, tuple[str | None, str]] = {}
            for idx, variant, level in code_cols:
                if row[idx] != "":
                    items[variant] = (level, row[idx])
            yield RawRow(row[1], items, row[i_value], row[i_note],
                         row[i_flag] if i_flag is not None else "")


@dataclass
class Series:
    series_id: str
    indicator_id: str
    breakdown: dict[str, str]


@dataclass
class Stats:
    rows: int = 0
    loaded: int = 0
    non_municipality: int = 0
    empty: int = 0
    notes: Counter = field(default_factory=Counter)
    reliability: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {"file_rows": self.rows, "municipality_values": self.loaded,
                "non_municipality_rows": self.non_municipality, "empty_values": self.empty,
                "notes": dict(self.notes), "reliability_flags": dict(self.reliability)}


class Release:
    """A downloaded release directory and the rules to interpret its rows."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not self.manifest.get("complete"):
            raise ValueError(f"{directory}: download is not complete")
        self.sada = json.loads((directory / "sada.json").read_text(encoding="utf-8"))
        self.meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        self.code: str = self.sada["kod"]
        self.selection = self.manifest["selection"]
        self.variants = {v["kod"]: v for v in self.sada["variantyDimenze"]}
        self._codelists: dict[str, dict] = {}
        self.series: dict[str, Series] = {}
        self.periods: dict[tuple[str, str], tuple[date, date]] = {}

    # ---- codelists -------------------------------------------------------
    def codelist(self, variant: str) -> dict:
        """{(level, code): item} plus {(None, code): item} for unambiguous codes."""
        if variant not in self._codelists:
            items = json.loads((self.dir / "dims" / f"{variant}.json").read_text(encoding="utf-8"))
            index: dict = {}
            seen = Counter(p["kod"] for p in items)
            for p in items:
                index[(p.get("kodUrovne"), p["kod"])] = p
                if seen[p["kod"]] == 1:
                    index[(None, p["kod"])] = p
            index["__len__"] = len(items)
            self._codelists[variant] = index
        return self._codelists[variant]

    def item(self, variant: str, level: str | None, code: str) -> dict:
        index = self.codelist(variant)
        found = index.get((level, code)) or index.get((None, code))
        if found is None:
            raise KeyError(f"{self.code}: item {code!r} (level {level}) not in codelist {variant}")
        return found

    def municipalities(self) -> dict[str, str]:
        """{code: name} of municipality items in the main territory codelist."""
        index = self.codelist(self.selection["territory"])
        return {k[1]: p["nazev"] for k, p in index.items()
                if isinstance(k, tuple) and k[0] == "OBEC"}

    # ---- row interpretation ---------------------------------------------
    def breakdown_key(self, variant: str) -> str:
        v = self.variants[variant]
        return BREAKDOWN_KEYS.get(v["typDimenzeKod"].upper(), v["nazev"].strip().lower())

    def interpret(self, row: RawRow, territory: str, level_wanted: str):
        """-> (series_id, territory code, (lower, upper)) or None if the row is
        not at the wanted territorial level."""
        level, terr_code = row.items[territory]
        terr = self.item(territory, level, terr_code)
        if (level or terr.get("kodUrovne")) != level_wanted:
            return None
        period = None
        parts: list[tuple[str, str, str, str]] = []  # (TYPE, code, key, label)
        for variant, (lvl, code) in row.items.items():
            if variant == territory:
                continue
            typ = self.variants[variant]["typDimenzeKod"]
            if typ == "REF_CAS":
                key = (variant, code)
                if key not in self.periods:
                    self.periods[key] = period_of(self.item(variant, lvl, code))
                period = self.periods[key]
                continue
            item = self.item(variant, lvl, code)
            # Totals and constant dimensions carry no information: the total
            # series of an indicator has an empty breakdown.
            if item.get("agregacniPolozka") or self.codelist(variant)["__len__"] == 1:
                continue
            parts.append((typ.upper(), code, self.breakdown_key(variant), item["nazev"].strip()))
        if period is None:
            raise ValueError(f"{self.code}: row without a time dimension: {row}")
        parts.sort()
        series_id = f"czso.{row.indicator}"
        if parts:
            series_id += "|" + ";".join(f"{t}={c}" for t, c, _, _ in parts)
        if series_id not in self.series:
            breakdown = {key: label for _, _, key, label in parts}
            if len(breakdown) != len(parts):
                raise ValueError(f"{self.code}: two dimensions share a breakdown key in {series_id}")
            self.series[series_id] = Series(series_id, f"czso.{row.indicator}", breakdown)
        return series_id, terr_code, period

    def observations(self, folder: str = "data", territory: str | None = None,
                     level: str = "OBEC", stats: Stats | None = None):
        """Yield (series_id, territory code, lower, upper, value string)."""
        territory = territory or self.selection["territory"]
        stats = stats if stats is not None else Stats()
        for path in sorted((self.dir / folder).glob("*.csv")):
            for row in read_csv(path):
                stats.rows += 1
                if row.note:
                    stats.notes[row.note] += 1
                if row.reliability:
                    stats.reliability[row.reliability] += 1
                hit = self.interpret(row, territory, level)
                if hit is None:
                    stats.non_municipality += 1
                    continue
                if row.value == "":
                    stats.empty += 1
                    continue
                if not DECIMAL.fullmatch(row.value):
                    raise ValueError(f"{path}: value {row.value!r} is not a plain decimal")
                stats.loaded += 1
                series_id, code, (lower, upper) = hit
                yield series_id, code, lower, upper, row.value
