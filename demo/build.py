"""Demo: a demographics page for one municipality, built from the database.

    uv run python demo/build.py [--obec 596230]

Writes chart_data/*.csv + *.json (the chart format of Charta, lib/charta) plus index.html
(topic menu) and demografie.html next to this file. Serve the repository root over HTTP
(the pages load ../lib/charta) and open http://localhost:8000/demo/:

    python -m http.server 8000

Each chart of the municipality is followed by a comparison with
  * municipalities of the same size band (population at 31. 12. of the latest year), and
  * all municipalities of its kraj.
A group's figures are sums over its members (rates = sum of events / sum of population).
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from ingest import config, db  # noqa: E402

OUT = HERE / "chart_data"

TYPE_LABEL = {
    "obec": "Obec", "mestys": "Městys", "mesto": "Město", "statutarni_mesto": "Statutární město",
    "krajske_mesto": "Krajské město", "hlavni_mesto": "Hlavní město",
}
AGE = {"0 - 14 let": "0–14 let", "15 - 64 let": "15–64 let", "65 a více let": "65 a více let"}
AGE_TABS = ["15 - 64 let", "65 a více let", "0 - 14 let"]  # order of age-group tabs in comparison charts
# size bands for the comparison group: [lower, upper) by population
BANDS = [0, 200, 500, 1000, 2000, 5000, 15000, 50000, 150000, 10**8]
AGE_PALETTE = ["#6250d6", "#eb6834", "#0f9aa8"]  # age groups: apart from the growth chart's blue/red
# section headings (also listed in the side navigation), placed before the chart that opens them
SECTIONS = {"obyvatele": ("pocet-obyvatel", "Počet obyvatel"),
            "vek-skupiny": ("vekova-struktura", "Věková struktura"),
            "migrace-vek": ("migrace", "Migrace")}
SMOOTH = 3  # years in the trailing moving average of rates

NBSP = " "


def fmt_int(v: float) -> str:
    return f"{v:,.0f}".replace(",", NBSP)


def band_label(lo: int, hi: int) -> str:
    k = lambda v: f"{v // 1000}{NBSP}tis." if v >= 1000 else str(v)  # noqa: E731
    if hi >= 10**8:
        return f"Obce nad {k(lo)} obyv."
    if lo >= 1000:
        return f"Obce {lo // 1000}–{hi // 1000}{NBSP}tis. obyv."
    return f"Obce {lo}–{k(hi)} obyv."


class Territory:
    """A municipality or a group of municipalities; values are sums over its members."""

    def __init__(self, conn, label: str, members: list[int]):
        self.conn, self.label, self.members = conn, label, members
        self._cache: dict = {}

    def get(self, indicator: str, breakdown: dict | None = None) -> dict[int, float]:
        key = (indicator, json.dumps(breakdown or {}, sort_keys=True))
        if key not in self._cache:
            rows = self.conn.execute(
                """SELECT extract(year FROM lower(o.period))::int, sum(o.value)
                   FROM current_observation o JOIN series s USING (series_key)
                   WHERE o.municipality_id = ANY(%s) AND s.indicator_id = %s AND s.breakdown = %s::jsonb
                     AND upper(o.period) - lower(o.period) BETWEEN 365 AND 366
                   GROUP BY 1 ORDER BY 1""",
                (self.members, indicator, key[1])).fetchall()
            self._cache[key] = {y: float(v) for y, v in rows}
        return self._cache[key]

    # --- building blocks
    def pop(self):  # 31. 12.
        return self.get("czso.2406K")

    def mid(self):  # 1. 7.
        return self.get("czso.9379W")

    def natural(self):
        return self.get("czso.5401")

    def migration(self):
        return self.get("czso.5402")

    def age_end(self, a=None):  # 31. 12., by basic age group (None = total)
        return self.get("czso.2406K2", {"věk": a} if a else None)

    def age_start(self, a):  # 1. 1.
        return self.get("czso.2406P", {"věk": a})

    def age_mid(self, a):  # mean of 1. 1. and 31. 12.
        s, e = self.age_start(a), self.age_end(a)
        return {y: (s[y] + e[y]) / 2 for y in s.keys() & e.keys()}

    def migration_age(self, a):
        return self.get("czso.5402W", {"věk": a})


def rate(events: dict, base: dict, per: float = 1000, smooth: int = SMOOTH) -> dict[int, float]:
    """events / base * per, over a trailing window of `smooth` years (ratio of sums)."""
    out = {}
    for y in sorted(events.keys() & base.keys()):
        window = range(y - smooth + 1, y + 1)
        if all(w in events and w in base for w in window):
            b = sum(base[w] for w in window)
            if b:
                out[y] = per * sum(events[w] for w in window) / b
    return out


def source(conn, dataset_id: str) -> dict:
    name, published = conn.execute(
        """SELECT d.dataset_name, max(r.published_at) FROM dataset d JOIN release r USING (dataset_id)
           WHERE dataset_id = %s GROUP BY 1""", (dataset_id,)).fetchone()
    return {"name": "ČSÚ", "dataset": f"{name} ({dataset_id}, zveřejněno {published:%d. %m. %Y})",
            "url": f"https://data.csu.gov.cz/datastat/info/SADA/{dataset_id}"}


def write_chart(id: str, rows: list[dict], *, type: str, title: str, subtitle: str,
                aes: list[dict], sources: list[dict], options: dict | None = None,
                tabs: list[dict] | None = None, note: str = "") -> str:
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(OUT / f"{id}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in rows)
    spec = {"id": id, "data": f"{id}.csv", "type": type, "title": title, "subtitle": subtitle,
            "aes": aes, "source": sources, "data_note": note,
            "options": {"timeline": False, **(options or {})},  # no year slider: the series are short
            "last_updated": date.today().isoformat(), "rows": len(rows)}
    if tabs:
        spec["tabs"] = tabs
    (OUT / f"{id}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--obec", type=int, default=596230)  # Nové Město na Moravě
    obec = ap.parse_args().obec
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*"):
        old.unlink()
    conn = db.connect(config.load())

    name, mtype = conn.execute(
        "SELECT municipality_name, municipality_type FROM municipality WHERE municipality_id = %s",
        (obec,)).fetchone()
    groups = {t: (gid, gname) for gid, gname, t in conn.execute(
        """SELECT g.group_id, g.group_name, g.group_type FROM municipality_group_member m
           JOIN municipality_group g USING (group_id) WHERE m.municipality_id = %s""", (obec,)).fetchall()}

    me = Territory(conn, name, [obec])
    pop = me.pop()
    last = max(pop)

    # comparison groups
    sizes = dict(conn.execute(
        """SELECT o.municipality_id, o.value FROM current_observation o JOIN series s USING (series_key)
           WHERE s.series_id = 'czso.2406K' AND lower(o.period) = make_date(%s, 1, 1)""", (last,)).fetchall())
    lo, hi = next((BANDS[i], BANDS[i + 1]) for i in range(len(BANDS) - 1)
                  if BANDS[i] <= pop[last] < BANDS[i + 1])
    size_members = [m for m, v in sizes.items() if lo <= v < hi]
    kraj_id, kraj_name = groups["kraj"]
    kraj_members = [m for (m,) in conn.execute(
        "SELECT municipality_id FROM municipality_group_member WHERE group_id = %s", (kraj_id,))]
    peers = [
        Territory(conn, f"{band_label(lo, hi)} ({len(size_members)})", size_members),
        Territory(conn, f"{kraj_name} (všechny obce)", kraj_members),
    ]
    terrs = [me, *peers]
    cmp_note = (f"Srovnávací skupiny: {len(size_members)} obcí s {lo:,}–{hi - 1:,} obyvateli k 31. 12. {last} "
                f"a všech {len(kraj_members)} obcí kraje (obě včetně obce {name}). Hodnoty skupin jsou "
                "součty za jejich obce.").replace(",", NBSP)
    smooth_note = f"Míry jsou vyhlazené {SMOOTH}letým klouzavým průměrem (součet událostí za {SMOOTH} roky / součet středních stavů)."

    x = {"type": "x", "field": "rok", "label": "Rok", "format": "year"}
    by_territory = {"type": "linetype", "field": "uzemi", "label": "Území"}  # obec solid, groups dashed
    src_oby = source(conn, "OBY01B01")
    src_age = source(conn, "OBY02E")
    src_mig = source(conn, "OBY06A")
    charts: list[str] = []

    # ---------- 1. population
    charts.append(write_chart(
        "obyvatele", [{"rok": y, "obyvatele": v} for y, v in pop.items()],
        type="line", title="Počet obyvatel", subtitle="Počet obyvatel k 31. 12. daného roku",
        aes=[x, {"type": "y", "field": "obyvatele", "label": "Počet obyvatel"}],
        sources=[src_oby], options={"y_zero": False}))

    rows = []
    for t in terrs:
        p = t.pop()
        base = p.get(2000) or p[min(p)]
        rows += [{"rok": y, "uzemi": t.label, "index": 100 * v / base} for y, v in p.items()]
    charts.append(write_chart(
        "obyvatele-srovnani", rows, type="line", title="Vývoj počtu obyvatel ve srovnání",
        subtitle="Počet obyvatel k 31. 12., index (rok 2000 = 100)",
        aes=[x, {"type": "y", "field": "index", "label": "Index (2000 = 100)"}, by_territory],
        sources=[src_oby], options={"y_zero": False, "annotations": [{"y": 100}]},
        note=cmp_note))

    # ---------- 2. growth
    nat, mig, mid = me.natural(), me.migration(), me.mid()
    rows = [{"rok": y, "slozka": s, "osoby": d[y], "podil": d[y] / mid[y] if mid.get(y) else None}
            for y in sorted(nat.keys() & mig.keys())
            for s, d in (("Přirozený přírůstek", nat), ("Čistá migrace", mig))]
    charts.append(write_chart(
        "prirustek", rows, type="bar", title="Populační růst",
        subtitle="Změna počtu obyvatel rozdělená na [přirozený přírůstek::Živě narození − zemřelí] "
                 "a [čistou migraci::Přistěhovalí − vystěhovalí]",
        tabs=[{"id": "abs", "label": "Osoby"}, {"id": "pct", "label": "% obyvatel"}],
        aes=[x, {"type": "y", "field": "osoby", "label": "Změna", "unit": "os.", "tab": "abs"},
             {"type": "y", "field": "podil", "label": "Změna", "format": "percent", "tab": "pct"},
             {"type": "fill", "field": "slozka", "label": "Složka"}],
        sources=[src_oby], note="Podíl = změna / počet obyvatel k 1. 7."))

    rows = []
    for t in terrs:
        nat_t, mig_t = t.natural(), t.migration()
        both = {y: nat_t[y] + mig_t[y] for y in nat_t.keys() & mig_t.keys()}
        n, m, c = (rate(d, t.mid()) for d in (nat_t, mig_t, both))
        rows += [{"rok": y, "uzemi": t.label, "celkem": c.get(y), "prirozeny": n.get(y), "migrace": m.get(y)}
                 for y in sorted(n.keys() | m.keys())]
    charts.append(write_chart(
        "prirustek-srovnani", rows, type="line", title="Populační růst ve srovnání",
        subtitle=f"Na 1 000 obyvatel, {SMOOTH}letý klouzavý průměr",
        tabs=[{"id": "tot", "label": "Celkový přírůstek"}, {"id": "nat", "label": "Přirozený přírůstek"},
              {"id": "mig", "label": "Čistá migrace"}],
        aes=[x, {"type": "y", "field": "celkem", "label": "Celkový přírůstek", "unit": "‰", "tab": "tot"},
             {"type": "y", "field": "prirozeny", "label": "Přirozený přírůstek", "unit": "‰", "tab": "nat"},
             {"type": "y", "field": "migrace", "label": "Čistá migrace", "unit": "‰", "tab": "mig"},
             by_territory],
        sources=[src_oby], note=f"{smooth_note} {cmp_note}"))

    # ---------- 3. age groups
    total = me.age_end()
    years = sorted(set(total).intersection(*(me.age_end(a) for a in AGE)))
    rows = [{"rok": y, "skupina": AGE[a], "osoby": me.age_end(a)[y], "podil": me.age_end(a)[y] / total[y]}
            for y in years for a in AGE]
    charts.append(write_chart(
        "vek-skupiny", rows, type="line", title="Věkové složení",
        subtitle="Obyvatelé v hlavních věkových skupinách k 31. 12.",
        tabs=[{"id": "pct", "label": "Podíl"}, {"id": "abs", "label": "Osoby"}],
        aes=[x, {"type": "y", "field": "podil", "label": "Podíl", "format": "percent", "tab": "pct"},
             {"type": "y", "field": "osoby", "label": "Počet obyvatel", "tab": "abs"},
             {"type": "colour", "field": "skupina", "label": "Věková skupina"}],
        sources=[src_age], options={"palette": AGE_PALETTE}))

    cols = {a: f"v{i}" for i, a in enumerate(AGE)}
    rows = []
    for t in terrs:
        tot = t.age_end()
        rows += [{"rok": y, "uzemi": t.label,
                  **{cols[a]: t.age_end(a)[y] / tot[y] if y in t.age_end(a) else None for a in AGE}}
                 for y in sorted(tot)]
    charts.append(write_chart(
        "vek-skupiny-srovnani", rows, type="line", title="Věkové složení ve srovnání",
        subtitle="Podíl obyvatel věkové skupiny k 31. 12., %",
        tabs=[{"id": cols[a], "label": AGE[a]} for a in AGE_TABS],
        aes=[x, *({"type": "y", "field": cols[a], "label": f"Podíl {AGE[a]}", "format": "percent",
                   "tab": cols[a]} for a in AGE), by_territory],
        sources=[src_age], options={"y_zero": False}, note=cmp_note))

    # ---------- 4. dependency
    def ratios(t: Territory) -> dict[str, dict[int, float]]:
        k, w, o = (t.age_end(a) for a in AGE)
        ys = sorted(k.keys() & w.keys() & o.keys())
        return {"Dětská": {y: 100 * k[y] / w[y] for y in ys},
                "Seniorská": {y: 100 * o[y] / w[y] for y in ys},
                "Celková": {y: 100 * (k[y] + o[y]) / w[y] for y in ys}}

    dep_note = ("Vypočteno z počtu obyvatel podle základních věkových skupin. Dětská = (0–14) / (15–64), "
                "seniorská = (65+) / (15–64), celková = součet obou; vše × 100.")
    mine = ratios(me)
    charts.append(write_chart(
        "zavislost", [{"rok": y, "index": k, "hodnota": v} for k, d in mine.items() for y, v in d.items()],
        type="line", title="Index závislosti",
        subtitle="Počet [dětí::0–14 let] a [seniorů::65 a více let] na 100 obyvatel "
                 "v produktivním věku (15–64 let), k 31. 12.",
        aes=[x, {"type": "y", "field": "hodnota", "label": "Na 100 osob 15–64 let"},
             {"type": "colour", "field": "index", "label": "Závislost"}],
        sources=[src_age], note=dep_note))

    dcols = {"Celková": "celkova", "Dětská": "detska", "Seniorská": "seniorska"}
    rows = []
    for t in terrs:
        r = ratios(t)
        rows += [{"rok": y, "uzemi": t.label, **{dcols[k]: r[k][y] for k in dcols}} for y in r["Celková"]]
    charts.append(write_chart(
        "zavislost-srovnani", rows, type="line", title="Index závislosti ve srovnání",
        subtitle="Na 100 obyvatel ve věku 15–64 let, k 31. 12.",
        tabs=[{"id": c, "label": k} for k, c in dcols.items()],
        aes=[x, *({"type": "y", "field": c, "label": f"{k} závislost", "tab": c} for k, c in dcols.items()),
             by_territory],
        sources=[src_age], options={"y_zero": False}, note=f"{dep_note} {cmp_note}"))

    # ---------- 5. net migration by age
    rows = []
    for y in sorted(set.intersection(*(set(me.migration_age(a)) for a in AGE))):
        for a in AGE:
            v = me.migration_age(a)[y]
            rows.append({"rok": y, "skupina": AGE[a], "osoby": v, "podil": v / mid[y] if mid.get(y) else None})
    charts.append(write_chart(
        "migrace-vek", rows, type="bar", title="Čistá migrace podle věku",
        subtitle="Přistěhovalí − vystěhovalí v hlavních věkových skupinách",
        tabs=[{"id": "abs", "label": "Osoby"}, {"id": "pct", "label": "% obyvatel"}],
        aes=[x, {"type": "y", "field": "osoby", "label": "Saldo", "unit": "os.", "tab": "abs"},
             {"type": "y", "field": "podil", "label": "Saldo", "format": "percent", "tab": "pct"},
             {"type": "fill", "field": "skupina", "label": "Věková skupina"}],
        sources=[src_mig], options={"palette": AGE_PALETTE},
        note="Podíl = saldo věkové skupiny / všichni obyvatelé k 1. 7., takže sloupec dává celkovou míru čisté migrace."))

    rows = []
    for t in terrs:
        r = {a: rate(t.migration_age(a), t.age_mid(a)) for a in AGE}
        rows += [{"rok": y, "uzemi": t.label, **{cols[a]: r[a].get(y) for a in AGE}}
                 for y in sorted(set().union(*r.values()))]
    charts.append(write_chart(
        "migrace-srovnani", rows, type="line", title="Míra čisté migrace podle věku ve srovnání",
        subtitle=f"Saldo migrace na 1 000 obyvatel dané věkové skupiny, {SMOOTH}letý klouzavý průměr",
        tabs=[{"id": cols[a], "label": AGE[a]} for a in AGE_TABS],
        aes=[x, *({"type": "y", "field": cols[a], "label": f"Čistá migrace {AGE[a]}", "unit": "‰",
                   "tab": cols[a]} for a in AGE), by_territory],
        sources=[src_mig, src_age],
        note=f"Obyvatelé věkové skupiny = průměr stavů k 1. 1. a 31. 12. {smooth_note} {cmp_note}"))

    # ---------- page
    # kraj names already carry the word ("Kraj Vysočina", "Jihomoravský kraj")
    facts = [
        f"{TYPE_LABEL.get(mtype, mtype or 'Obec')}, {fmt_int(pop[last])} obyvatel (31. 12. {last})",
        f"{kraj_name}, SO ORP {groups.get('SO ORP', (None, '–'))[1]}",
    ]

    def fill(template: str) -> str:  # the municipality header shared by both pages
        page = (HERE / template).read_text(encoding="utf-8")
        return (page.replace("{{name}}", html.escape(name)).replace("{{code}}", str(obec))
                .replace("{{facts}}", "\n".join(f'                    <p>{html.escape(v)}</p>' for v in facts)))

    (HERE / "index.html").write_text(fill("template-menu.html"), encoding="utf-8")
    page = (fill("template.html")
            .replace("{{charts}}", "\n".join(  # each chart followed by its comparison
                (f'                <h2 id="{SECTIONS[a][0]}">{SECTIONS[a][1]}</h2>\n' if a in SECTIONS else "")
                + f'                <section class="pair">\n'
                f'                    <figure data-chart="{a}"></figure>\n'
                f'                    <figure data-chart="{b}" class="compare"></figure>\n'
                f'                </section>'
                for a, b in zip(charts[::2], charts[1::2]))))
    (HERE / "demografie.html").write_text(page, encoding="utf-8")
    print(f"{name}: {len(charts)} charts; peers: {', '.join(p.label for p in peers)}")


if __name__ == "__main__":
    main()
