"""Download ČSÚ datasets to the raw data folder.

Layout:  {data_folder}/czso/{SADA}/v{verze}_imp{id}/
    sada.json        catalogue definition of the dataset
    metadata.json    content metadata (publication time, content id)
    dims/{kod}.json  codelists of the dimension variants used
    data/*.csv       municipality-level data, exactly as returned
    orp/*.csv        SO ORP-level data (used only to validate aggregation)
    manifest.json    every file with URL, parameters, size and sha256;
                     "complete": true once the whole release is on disk

A release directory is immutable once complete: nothing is re-downloaded.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .client import DOTAZ, KATALOG, ApiError, Client, TooLarge

HERE = Path(__file__).resolve().parent
CELL_BUDGET = 150_000  # the API refuses synchronous CSV somewhere above ~170k cells
INDICATOR = "IndicatorType"


def load_datasets() -> dict:
    return yaml.safe_load((HERE / "datasets.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Choosing dimension variants
# --------------------------------------------------------------------------

@dataclass
class Selection:
    """Which dimension variants of a dataset we query."""
    territory: str                      # main variant (most municipalities)
    orp: str | None                     # variant carrying SO ORP level, if any
    time: list[str]                     # time variants
    other: dict[str, str] = field(default_factory=dict)  # TYPE -> variant code


def _levels(variant: dict) -> dict[str, int]:
    return {u["kodUrovne"]: u.get("pocetPolozek") or 0 for u in variant["urovneDimenze"]}


def _n_items(variant: dict) -> int:
    return sum(_levels(variant).values())


def choose_selection(sada: dict, override: dict) -> Selection:
    variants = [v for v in sada["variantyDimenze"] if not v.get("skrytaVariantaDimenze")]
    by_code = {v["kod"]: v for v in variants}
    rules = sada.get("pravidlaVyberu") or {}
    forbidden = [set(pair) for pair in rules.get("zakazaneKombinace") or []]
    mandatory = set(rules.get("povinneDimenze") or [])

    def allowed(code: str, chosen: list[str]) -> bool:
        return not any({code, c} in forbidden for c in chosen)

    territories = [v for v in variants if v["typDimenzeKod"] == "VUZEMI"]
    with_obec = [v for v in territories if _levels(v).get("OBEC", 0) > 1]
    if not with_obec:
        raise ValueError(f"{sada['kod']}: no municipality-level territory variant")
    if override.get("territory"):
        territory = by_code[override["territory"]]
    else:
        # most municipalities; among equals the one with the least other levels
        territory = max(with_obec, key=lambda v: (_levels(v)["OBEC"], -_n_items(v)))

    with_orp = [v for v in territories if "ORP" in _levels(v)]
    orp = min(with_orp, key=_n_items)["kod"] if with_orp else None

    time = [v["kod"] for v in variants if v["typDimenzeKod"] == "REF_CAS"]
    if override.get("time"):
        time = [t for t in time if t in override["time"]]

    other: dict[str, str] = {}
    chosen = [territory["kod"]]
    skip = set(override.get("skip_dims") or [])
    types = sorted({v["typDimenzeKod"] for v in variants} - {"VUZEMI", "REF_CAS"} - skip)
    for typ in types:
        candidates = [v for v in variants if v["typDimenzeKod"] == typ]
        wanted = (override.get("dims") or {}).get(typ)
        if wanted:
            candidates = [by_code[wanted]]
        else:
            candidates.sort(key=_n_items, reverse=True)  # most detailed first
        pick = next((v for v in candidates if allowed(v["kod"], chosen)), None)
        if pick is None:
            if typ in mandatory:
                raise ValueError(f"{sada['kod']}: mandatory dimension {typ} not combinable")
            continue  # e.g. sex is not published at municipality level
        other[typ] = pick["kod"]
        chosen.append(pick["kod"])
    return Selection(territory["kod"], orp, time, other)


# --------------------------------------------------------------------------
# Planning requests under the size limit
# --------------------------------------------------------------------------

def plan_requests(axes: dict[str, list[str]], fixed_cells: int, budget: int = CELL_BUDGET) -> list[dict[str, list[str]]]:
    """Split the cross product of `axes` (each a list of item codes) into
    requests of at most `budget` cells. `fixed_cells` is the number of
    territory items, which is never split. Axes are split in the order given
    (time first), so a request holds whole periods whenever possible."""
    cells = fixed_cells * math.prod(len(v) for v in axes.values())
    if cells <= budget:
        return [axes]
    for name, items in axes.items():
        if len(items) <= 1:
            continue
        per_item = cells / len(items)
        size = max(1, int(budget // per_item))
        out: list[dict[str, list[str]]] = []
        for i in range(0, len(items), size):
            out.extend(plan_requests({**axes, name: items[i:i + size]}, fixed_cells, budget))
        return out
    return [axes]  # cannot split further; let the API decide


def time_items(variant_code: str, polozky: list[dict], meta: dict) -> list[str]:
    """Codes of the time items that can hold data, per content metadata."""
    items = sorted(polozky, key=lambda p: p["poradi"])
    for dim in meta.get("dimenze") or []:
        if dim["kod"] == variant_code:
            ranges = dim.get("rozsahHodnot") or []
            if ranges:
                lo = min(r["poradiPolozkyOd"] for r in ranges)
                hi = max(r["poradiPolozkyDo"] for r in ranges)
                return [p["kod"] for p in items if lo <= p["poradi"] <= hi]
    return [p["kod"] for p in items]


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


class ReleaseDownload:
    def __init__(self, client: Client, root: Path, code: str, override: dict, log=print):
        self.client, self.code, self.override, self.log = client, code, override, log
        self.sada = client.get_json(f"{KATALOG}/sady/{code}")
        self.verze = self.sada["verze"]
        self.meta = self._content_meta()
        self.dir = root / "czso" / code / f"v{self.verze}_imp{self.meta['idPripravenyImport']}"
        self.manifest_path = self.dir / "manifest.json"
        self.manifest = (
            json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if self.manifest_path.exists()
            else {"dataset": code, "verze": self.verze,
                  "import_id": self.meta["idPripravenyImport"], "complete": False, "files": {}}
        )

    def _content_meta(self) -> dict:
        return self.client.get_json(f"{DOTAZ}/metadata/sady/{self.code}", {"verze": self.verze})

    def _save_manifest(self) -> None:
        _write(self.manifest_path, json.dumps(self.manifest, ensure_ascii=False, indent=1).encode())

    def _record(self, rel: str, data: bytes, url: str, params: dict | None) -> None:
        _write(self.dir / rel, data)
        self.manifest["files"][rel] = {
            "url": url, "params": params, "bytes": len(data), "sha256": _sha256(data),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save_manifest()

    def _json_file(self, rel: str, url: str, params: dict | None = None, payload=None):
        if rel in self.manifest["files"] and (self.dir / rel).exists():
            return json.loads((self.dir / rel).read_text(encoding="utf-8"))
        if payload is None:
            data = self.client.get(url, params).content
        else:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self._record(rel, data, url, params)
        return json.loads(data)

    def _fetch_csv(self, folder: str, params: dict[str, str]) -> None:
        key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
        rel = f"{folder}/{key}.csv"
        if rel in self.manifest["files"] and (self.dir / rel).exists():
            return
        url = f"{DOTAZ}/data/sady/{self.code}/vlastni"
        data = self.client.get_bytes(url, params)
        if not data.startswith(b'"Ukazatel"'):
            raise RuntimeError(f"{self.code}: unexpected CSV start {data[:80]!r}")
        self._record(rel, data, url, params)

    def _download_axes(self, folder: str, territory_param: dict[str, str],
                       axes: dict[str, list[str]], full: dict[str, int], fixed_cells: int,
                       budget: int = CELL_BUDGET) -> None:
        for req in plan_requests(axes, fixed_cells, budget):
            params = {"verzeSady": self.verze, "format": "CSV", "kodZvlast": "true", **territory_param}
            for name, items in req.items():
                # '*' when the whole codelist is requested, explicit codes otherwise
                params[name] = "*" if len(items) == full[name] and name != INDICATOR else ",".join(items)
            if INDICATOR in req and len(req[INDICATOR]) == full[INDICATOR]:
                params.pop(INDICATOR)  # all indicators is the default
            try:
                self._fetch_csv(folder, params)
            except TooLarge as exc:
                if budget < 2_000:
                    raise
                self.log(f"  {self.code}: {exc}; splitting further")
                self._download_axes(folder, territory_param, req, full, fixed_cells, budget // 2)

    def run(self) -> Path:
        if self.manifest.get("complete"):
            self.log(f"{self.code}: v{self.verze} import {self.meta['idPripravenyImport']} already on disk")
            return self.dir
        self._json_file("sada.json", f"{KATALOG}/sady/{self.code}", payload=self.sada)
        self._json_file("metadata.json", f"{DOTAZ}/metadata/sady/{self.code}",
                        {"verze": self.verze}, payload=self.meta)
        sel = choose_selection(self.sada, self.override)
        self.manifest["selection"] = sel.__dict__
        by_code = {v["kod"]: v for v in self.sada["variantyDimenze"]}

        def codelist(code: str) -> list[dict]:
            return self._json_file(f"dims/{code}.json", f"{KATALOG}/dimenze/{code}/polozky")

        codelist(sel.territory)
        banned = (self.sada.get("pravidlaVyberu") or {}).get("zakazaneDimenzeUkazatelu") or []

        def indicators_for(territory: str) -> list[str]:
            """Indicators publishable with the chosen variants."""
            used = {territory, *sel.other.values()}
            out = {b["ukazatelKod"] for b in banned if used & set(b["zakazaneVariantyDimenze"])}
            return [u["kod"] for u in self.sada["ukazatele"] if u["kod"] not in out]

        other_axes = {v: [p["kod"] for p in codelist(v)] for v in sel.other.values()}

        for tvar in sel.time:
            periods = time_items(tvar, codelist(tvar), self.meta)
            axes = {tvar: periods, INDICATOR: indicators_for(sel.territory), **other_axes}
            full = {k: len(v) for k, v in axes.items()}
            full[tvar] = -1  # never '*' for time: only periods that hold data
            self.log(f"{self.code}: {tvar} {periods[0]}–{periods[-1]} "
                     f"({len(periods)} periods, {len(plan_requests(axes, _n_items(by_code[sel.territory])))} requests)")
            self._download_axes("data", {sel.territory: "*"}, axes, full,
                                _n_items(by_code[sel.territory]))
            if sel.orp:
                orp_levels = _levels(by_code[sel.orp])
                codelist(sel.orp)
                tparam = ({sel.orp: "*"} if set(orp_levels) == {"ORP"}
                          else {f"{sel.orp}.doUrovne": "ORP"})
                orp_axes = {**axes, INDICATOR: indicators_for(sel.orp)}
                orp_full = {**full, INDICATOR: len(orp_axes[INDICATOR])}
                try:  # validation data only: never block the release on it
                    self._download_axes("orp", tparam, orp_axes, orp_full, _n_items(by_code[sel.orp]))
                except ApiError as exc:
                    self.log(f"  {self.code}: no ORP-level data for {tvar}: {exc}")
                    self.manifest.setdefault("orp_errors", {})[tvar] = str(exc)[:300]

        # The content must not have changed while we were downloading.
        after = self._content_meta()
        if after["idPripravenyImport"] != self.meta["idPripravenyImport"]:
            raise RuntimeError(f"{self.code}: content changed during download; run again")
        self.manifest["complete"] = True
        self._save_manifest()
        return self.dir


def download(client: Client, root: Path, codes: list[str] | None = None, log=print) -> list[Path]:
    cfg = load_datasets()["datasets"]
    out = []
    for code in codes or list(cfg):
        try:
            out.append(ReleaseDownload(client, root, code, cfg.get(code) or {}, log).run())
        except ApiError as exc:
            if "Nenaplněná datová sada" not in str(exc):
                raise
            # the current version of the dataset has no published content
            log(f"{code}: ČSÚ reports the dataset as not filled; nothing to download")
    return out


def latest_release_dir(root: Path, code: str) -> Path | None:
    """Most recently completed release directory of a dataset."""
    best: tuple[int, Path] | None = None
    for d in (root / "czso" / code).glob("v*_imp*"):
        mf = d / "manifest.json"
        if not mf.exists():
            continue
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m.get("complete") and (best is None or m["import_id"] > best[0]):
            best = (m["import_id"], d)
    return best[1] if best else None
