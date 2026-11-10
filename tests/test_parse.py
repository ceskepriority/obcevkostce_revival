import json
from datetime import date
from pathlib import Path

import pytest

from ingest.czso.download import choose_selection, plan_requests
from ingest.czso.parse import Release, Stats, period_of, read_csv, to_local_date

HEADER_FLAT = '"Ukazatel","IndicatorType","Pohlaví","POHL2","Rok","CasR","Obce","UZ5","Hodnota","MJ_TEXT","MJ_SYMBOL","POZNAMKA","POZNAMKA_TEXT"\n'
HEADER_HIER = '"Ukazatel","IndicatorType","Rok","CasR","SO ORP a obce-SO ORP","Uz45.ORP","SO ORP a obce-Obec","Uz45.OBEC","Hodnota","MJ_TEXT","MJ_SYMBOL","POZNAMKA","POZNAMKA_TEXT"\n'


def make_release(tmp_path: Path, territory: str, csv_text: str, other: dict) -> Release:
    d = tmp_path / "rel"
    (d / "data").mkdir(parents=True)
    (d / "dims").mkdir()
    (d / "data" / "a.csv").write_text(csv_text, encoding="utf-8")
    variants = [
        {"kod": "CasR", "typDimenzeKod": "REF_CAS", "nazev": "Roky"},
        {"kod": territory, "typDimenzeKod": "VUZEMI", "nazev": "Území"},
        {"kod": "POHL2", "typDimenzeKod": "POHLAVI", "nazev": "Pohlaví"},
    ]
    (d / "sada.json").write_text(json.dumps(
        {"kod": "TEST", "verze": "1", "nazev": "Test", "variantyDimenze": variants, "ukazatele": []}))
    (d / "metadata.json").write_text(json.dumps({"idPripravenyImport": 1, "casZverejneni": "2026-01-01T00:00:00Z"}))
    (d / "manifest.json").write_text(json.dumps({
        "complete": True, "files": {},
        "selection": {"territory": territory, "orp": None, "time": ["CasR"], "other": other}}))
    (d / "dims" / "CasR.json").write_text(json.dumps([
        {"kod": "2024", "casOd": "2023-12-31T23:00:00Z", "casDo": "2024-12-31T23:00:00Z"}]))
    (d / "dims" / "POHL2.json").write_text(json.dumps([
        {"kod": "0", "nazev": "Celkem", "agregacniPolozka": True},
        {"kod": "1", "nazev": "Muži", "agregacniPolozka": False},
        {"kod": "2", "nazev": "Ženy", "agregacniPolozka": False}]))
    (d / "dims" / f"{territory}.json").write_text(json.dumps([
        {"kod": "4101", "nazev": "Aš", "kodUrovne": "ORP"},
        {"kod": "554979", "nazev": "Abertamy", "kodUrovne": "OBEC"},
        {"kod": "554499", "nazev": 'Aš "město"', "kodUrovne": "OBEC"}]))
    return Release(d)


def test_dates():
    assert to_local_date("2023-12-31T23:00:00Z") == date(2024, 1, 1)
    assert to_local_date("2024-06-30T22:00:00Z") == date(2024, 7, 1)  # summer time
    assert period_of({"kod": "2024", "casOd": "2023-12-31T23:00:00Z", "casDo": "2024-12-31T23:00:00Z"}) \
        == (date(2024, 1, 1), date(2025, 1, 1))
    assert period_of({"kod": "1980-Q2", "casOd": "1980-03-31T23:00:00Z", "casDo": "1980-06-30T22:00:00Z"}) \
        == (date(1980, 4, 1), date(1980, 7, 1))
    # census reference day becomes a one-day period
    assert period_of({"kod": "C20210326", "casOd": "2021-03-25T23:00:00Z", "casDo": "2021-03-25T23:00:00Z"}) \
        == (date(2021, 3, 26), date(2021, 3, 27))
    assert period_of({"kod": "2022"}) == (date(2022, 1, 1), date(2023, 1, 1))
    with pytest.raises(ValueError):
        period_of({"kod": "kolo1"})


def test_flat_file_values_are_untouched(tmp_path):
    text = HEADER_FLAT + (
        '"Počet","P","Celkem","0","2024","2024","Abertamy","554979","839.0","osoba","os.","",""\n'
        '"Počet","P","Muži","1","2024","2024","Abertamy","554979","-4.50","osoba","os.","",""\n'
        '"Počet","P","Ženy","2","2024","2024","Aš ""město""","554499","12.27436823","osoba","os.","p","předběžný"\n'
        '"Počet","P","Ženy","2","2024","2024","Abertamy","554979","","osoba","os.","",""\n')
    rel = make_release(tmp_path, "UZ5", text, {"POHLAVI": "POHL2"})
    stats = Stats()
    rows = list(rel.observations(stats=stats))
    assert rows == [
        ("czso.P", "554979", date(2024, 1, 1), date(2025, 1, 1), "839.0"),
        ("czso.P|POHLAVI=1", "554979", date(2024, 1, 1), date(2025, 1, 1), "-4.50"),
        ("czso.P|POHLAVI=2", "554499", date(2024, 1, 1), date(2025, 1, 1), "12.27436823"),
    ]
    assert rel.series["czso.P"].breakdown == {}           # total has an empty breakdown
    assert rel.series["czso.P|POHLAVI=1"].breakdown == {"pohlaví": "Muži"}
    assert (stats.rows, stats.loaded, stats.empty, stats.non_municipality) == (4, 3, 1, 0)
    assert stats.notes == {"p": 1}


def test_hierarchical_file_keeps_only_municipalities(tmp_path):
    text = HEADER_HIER + (
        '"Počet","P","2024","2024","Aš","4101","","","100.0","","","",""\n'
        '"Počet","P","2024","2024","Aš","4101","Abertamy","554979","40.0","","","",""\n')
    rel = make_release(tmp_path, "Uz45", text, {})
    stats = Stats()
    assert [r[1:] for r in rel.observations(stats=stats)] == [
        ("554979", date(2024, 1, 1), date(2025, 1, 1), "40.0")]
    assert stats.non_municipality == 1
    # the same file read at SO ORP level, for the aggregation check
    assert [r[1] for r in rel.observations(territory="Uz45", level="ORP")] == ["4101"]


def test_rejects_non_decimal_and_ragged_rows(tmp_path):
    rel = make_release(tmp_path, "UZ5", HEADER_FLAT +
                       '"Počet","P","Muži","1","2024","2024","Abertamy","554979","1e3","","","",""\n',
                       {"POHLAVI": "POHL2"})
    with pytest.raises(ValueError, match="plain decimal"):
        list(rel.observations())
    bad = tmp_path / "bad.csv"
    bad.write_text(HEADER_FLAT + '"Počet","P","Muži"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="fields"):
        list(read_csv(bad))


def test_plan_requests_respects_budget_and_covers_everything():
    axes = {"CasR": [str(y) for y in range(2000, 2026)], "IndicatorType": list("abcdefghi"), "POHL2": ["0", "1", "2"]}
    plan = plan_requests(axes, fixed_cells=6271, budget=150_000)
    cells = set()
    for req in plan:
        size = 6271
        for items in req.values():
            size *= len(items)
        assert size <= 150_000
        for y in req["CasR"]:
            for i in req["IndicatorType"]:
                for p in req["POHL2"]:
                    assert (y, i, p) not in cells   # no overlap
                    cells.add((y, i, p))
    assert len(cells) == 26 * 9 * 3                 # nothing left out


def test_choose_selection_skips_forbidden_dimension():
    def var(kod, typ, levels):
        return {"kod": kod, "typDimenzeKod": typ, "nazev": kod,
                "urovneDimenze": [{"kodUrovne": k, "pocetPolozek": n} for k, n in levels.items()]}
    sada = {"kod": "X", "variantyDimenze": [
        var("CasR", "REF_CAS", {"CAS_R": 61}),
        var("UZ25", "VUZEMI", {"KRAJ": 14, "OBEC": 6258}),
        var("UZALL", "VUZEMI", {"OBEC": 6271}),
        var("Uz4A", "VUZEMI", {"ORP": 205}),
        var("POHL2", "POHLAVI", {"#DEFAULT": 3})],
        "pravidlaVyberu": {"zakazaneKombinace": [["UZALL", "POHL2"]], "povinneDimenze": ["VUZEMI"]}}
    sel = choose_selection(sada, {})
    assert (sel.territory, sel.orp, sel.time, sel.other) == ("UZALL", "Uz4A", ["CasR"], {})


def test_rules_cover_codes_with_underscores_as_text():
    from ingest.czso.indicators import load_rules
    rules = load_rules()
    assert "5758_5" in rules and "57585" not in rules
    assert rules["5758_5"]["agg"] == "SUM" and rules["5758_5"]["norm"] == "2406K"
