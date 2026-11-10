"""Municipalities and their groups from ČSÚ territorial codelists.

The codelists are saved under {data_folder}/czso/_geography/{date}/ and the
database is filled from those files.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .client import KATALOG, Client

# variant code -> what we take from it
CODELISTS = {
    "UZ5ALLOKR": "all municipalities, including former ones and military areas",
    "UZ25": "municipality -> kraj",
    "UZ35H": "municipality -> okres",
    # SO ORP membership in the structure valid from 2026 (three municipalities
    # changed SO ORP on 1 Jan 2026; the undated variant Uz45B still shows 2025).
    # Revisit when ČSÚ publishes a newer structure.
    "UZ45R26B": "municipality -> SO ORP",
    "Uz24h": "SO ORP -> kraj",
    "Uz0123h2": "okres -> kraj -> stát",
    "UZ5M": "towns (města)",
    "UZ0KRAJME": "regional capitals",
}
GROUP_TYPES = {"STAT": "stát", "KRAJ": "kraj", "OKRES": "okres", "ORP": "SO ORP"}

# Statutární města (§ 4 zákona o obcích), Praha aside.
STATUTORY = [
    "Kladno", "Mladá Boleslav", "České Budějovice", "Plzeň", "Karlovy Vary",
    "Ústí nad Labem", "Děčín", "Chomutov", "Most", "Teplice", "Liberec",
    "Jablonec nad Nisou", "Hradec Králové", "Pardubice", "Jihlava", "Brno",
    "Olomouc", "Prostějov", "Přerov", "Zlín", "Ostrava", "Opava", "Havířov",
    "Karviná", "Frýdek-Místek", "Třinec",
]
PRAHA = "554782"
OKRES_SUFFIX = re.compile(r" \(okr\. [^)]*\)$")


def fetch(client: Client, root: Path, log=print) -> Path:
    directory = root / "czso" / "_geography" / date.today().isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    for code in CODELISTS:
        path = directory / f"{code}.json"
        if not path.exists():
            path.write_bytes(client.get(f"{KATALOG}/dimenze/{code}/polozky").content)
            log(f"geography: fetched {code}")
    return directory


def latest(root: Path) -> Path:
    dirs = sorted((root / "czso" / "_geography").glob("*-*-*"))
    if not dirs:
        raise SystemExit("No geography codelists on disk; run `czso geography` first.")
    return dirs[-1]


def build(directory: Path) -> dict:
    """-> {municipalities: {id: (name, type)}, groups: {id: (name, type, parent)},
           members: {(group_id, municipality_id)}}"""
    cl = {c: json.loads((directory / f"{c}.json").read_text(encoding="utf-8")) for c in CODELISTS}

    def clean(name: str) -> str:
        return OKRES_SUFFIX.sub("", name).strip()

    names: dict[str, str] = {}
    for code in ("UZ5ALLOKR", "UZ35H", "UZ45R26B", "UZ25"):  # later lists win: plain names
        for p in cl[code]:
            if p.get("kodUrovne") == "OBEC":
                names[p["kod"]] = clean(p["nazev"])

    towns = {p["kod"]: clean(p["nazev"]) for p in cl["UZ5M"]}
    capitals = {p["kod"] for p in cl["UZ0KRAJME"]}
    by_name: dict[str, list[str]] = {}
    for code, name in towns.items():
        by_name.setdefault(name, []).append(code)
    statutory = set()
    for name in STATUTORY:
        codes = by_name.get(name, [])
        if len(codes) != 1:
            raise ValueError(f"statutory city {name!r}: expected one town, found {codes}")
        statutory.add(codes[0])
    if PRAHA not in names or names[PRAHA] != "Praha":
        raise ValueError("Praha not found under its expected code")

    def mtype(code: str) -> str | None:
        if code == PRAHA:
            return "hlavni_mesto"
        if code in capitals:
            return "krajske_mesto"
        if code in statutory:
            return "statutarni_mesto"
        if code in towns:
            return "mesto"
        return None  # obec or městys: not distinguishable from DataStat codelists

    municipalities = {code: (name, mtype(code)) for code, name in names.items()}

    groups: dict[str, tuple[str, str, str | None]] = {}
    members: set[tuple[str, str]] = set()

    def add_group(p: dict, parent: str | None) -> None:
        gtype = GROUP_TYPES[p["kodUrovne"]]
        known = groups.get(p["kod"])
        if known and known[1] != gtype:
            raise ValueError(f"group code {p['kod']} is used for both {known[1]} and {gtype}")
        if known and known[2] and not parent:
            parent = known[2]
        groups[p["kod"]] = (p["nazev"].strip(), gtype, parent)

    def kraj_of(p: dict, index: dict[int, dict]) -> str | None:
        while p.get("nadrazenaPolozkaId") in index:
            p = index[p["nadrazenaPolozkaId"]]
            if p.get("kodUrovne") == "KRAJ":
                return p["kod"]
        return None

    state = next(p for p in cl["Uz0123h2"] if p.get("kodUrovne") == "STAT")
    add_group(state, None)
    for code in ("Uz0123h2", "Uz24h"):
        index = {p["identifikator"]: p for p in cl[code]}
        for p in cl[code]:
            level = p.get("kodUrovne")
            if level == "KRAJ":
                add_group(p, state["kod"])
            elif level in ("OKRES", "ORP"):
                add_group(p, kraj_of(p, index))

    for code in ("UZ25", "UZ35H", "UZ45R26B"):
        index = {p["identifikator"]: p for p in cl[code]}
        for p in cl[code]:
            if p.get("kodUrovne") != "OBEC":
                if p.get("kodUrovne") in GROUP_TYPES and p["kod"] not in groups:
                    add_group(p, None)
                continue
            parent = index.get(p.get("nadrazenaPolozkaId"))
            if parent is None or parent.get("kodUrovne") not in GROUP_TYPES:
                raise ValueError(f"{code}: municipality {p['kod']} has no parent group")
            members.add((parent["kod"], p["kod"]))
    for code in municipalities:
        members.add((state["kod"], code))

    clash = set(groups) & set(municipalities)
    if clash:
        raise ValueError(f"codes used for both a group and a municipality: {sorted(clash)[:5]}")
    return {"municipalities": municipalities, "groups": groups, "members": members}


def load(conn, directory: Path, log=print) -> None:
    geo = build(directory)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO municipality (municipality_id, municipality_name, municipality_type)
               VALUES (%s::int, %s, %s::municipality_type)
               ON CONFLICT (municipality_id) DO UPDATE
               SET municipality_name = EXCLUDED.municipality_name,
                   municipality_type = EXCLUDED.municipality_type
               WHERE (municipality.municipality_name, municipality.municipality_type)
                     IS DISTINCT FROM (EXCLUDED.municipality_name, EXCLUDED.municipality_type)""",
            [(c, n, t) for c, (n, t) in geo["municipalities"].items()],
        )
        # parents first (stát, then kraj, then the rest)
        order = {"stát": 0, "kraj": 1}
        rows = sorted(geo["groups"].items(), key=lambda kv: order.get(kv[1][1], 2))
        cur.executemany(
            """INSERT INTO municipality_group (group_id, group_name, group_type, parent_group_id)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (group_id) DO UPDATE
               SET group_name = EXCLUDED.group_name, group_type = EXCLUDED.group_type,
                   parent_group_id = EXCLUDED.parent_group_id""",
            [(g, n, t, p) for g, (n, t, p) in rows],
        )
        cur.execute("DELETE FROM municipality_group_member WHERE group_id = ANY(%s)",
                    (list(geo["groups"]),))
        cur.executemany(
            "INSERT INTO municipality_group_member (group_id, municipality_id) VALUES (%s, %s::int)",
            sorted(geo["members"]),
        )
    types: dict = {}
    for _, t in geo["municipalities"].values():
        types[t] = types.get(t, 0) + 1
    gtypes: dict = {}
    for _, t, _ in geo["groups"].values():
        gtypes[t] = gtypes.get(t, 0) + 1
    log(f"geography: {len(geo['municipalities'])} municipalities {types}, "
        f"groups {gtypes}, {len(geo['members'])} memberships")
