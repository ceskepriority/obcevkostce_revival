# Obce v kostce

See [spec.md](spec.md) for what this is.

## Database

Migrations live in `migrations/` and are applied with [goose](https://github.com/pressly/goose)
by the database owner:

```
goose -dir migrations postgres "<owner connection string>" up
```

## Ingestion

Python 3.12+, managed with [uv](https://docs.astral.sh/uv/). Settings are in `config.yaml`
(`data_folder` for raw files, `connection` for the database; optional `user_agent`
and `request_delay_s`).

### ČSÚ (DataStat API)

```
uv run python -m ingest czso download            # raw files -> {data_folder}/czso/
uv run python -m ingest czso geography           # municipalities, kraje, okresy, SO ORP
uv run python -m ingest czso indicators          # indicators + aggregation rules
uv run python -m ingest czso load                # raw files -> database (commit first)
uv run python -m ingest czso verify              # database == raw files == API
uv run python -m ingest czso check-aggregation   # rules vs ČSÚ's own SO ORP figures
```

Every command takes `--dataset CODE` (repeatable) to work on a subset. Running
`download` and `load` again later picks up new ČSÚ releases only; nothing already
on disk or in the database is fetched or written twice.

- `ingest/czso/datasets.yaml` — which datasets are ingested and their priority tier.
- `ingest/czso/indicators.yaml` — how each indicator aggregates and what it is
  normalised by. A new indicator without a rule stops the load.
- Raw files are kept exactly as received, with a `manifest.json` (URL, time,
  sha256) per release. Reports from `load`, `verify` and `check-aggregation`
  are written to `{data_folder}/czso/_reports/`.

Tests: `uv run pytest`.

## Comparison groups

Dashboards compare a municipality with a group of municipalities
(`migrations/00009_comparison_groups.sql`):

- **Preset groups** — stát, kraj, okres, SO ORP and nine size bands (`group_type = 'velikost'`,
  population at 31. 12. of the latest year; bounds in `size_band`). Their values are
  precomputed in the materialised view `group_observation`.
- **Custom sets** — any mix of groups and individual obce, stored with
  `SELECT comparison_set_save('{"name": "Sousedé", "groups": ["7209"], "obce": [596230]}')`, which
  returns a short stable id (the optional name is part of it, `00010_comparison_set_name.sql`); `comparison_set_members(id)` gives its obce with current membership.
  Values on demand: `aggregate_members(<obce>, <series keys>)`.

Both go through `aggregate_observation()`, which applies each indicator's aggregation rule
(SUM, MEAN, WEIGHTED_MEAN; NONE is not aggregated) and returns `n_values`, the number of
members that contributed. `refresh_current_observation()` (run by `load`, `refresh` and
`geography`) refreshes `current_observation`, the size bands and `group_observation`.

The web app connects as `ovk_web` (create the role before migrating, or re-run the grants);
it can read everything and write only through `comparison_set_save()`.

## Web app

Go + [templ](https://templ.guide) + HTMX + Alpine in `web/`; charts are drawn by
[Charta](https://github.com/MatousFiala/charta), a submodule in `lib/charta`
(`git submodule update --init` after cloning). It connects with `web_connection` from
`config.yaml` (or `OVK_WEB_CONNECTION`).

```
cd web
go tool templ generate     # after editing views/*.templ (the generated *_templ.go are committed)
go run .                   # http://localhost:8080
go test ./...
```

Flow: `/` pick an obec → `/obec/{id}/vyber` pick the comparison → `/obec/{id}/{selection}/`
menu → `/obec/{id}/{selection}/demografie`. The selection lives in the URL as comma-separated
items, each one line in the comparison charts: `g.<group_id>` (kraj, SO ORP, okres, size band,
stát), `o.<obec>`, `s.<comparison_set id>` (a named custom group, summed into one line).
Chart data is served at `…/{selection}/data/{chart}.json|.csv`; `…/{selection}/data.xlsx` is the
Excel export: an Info sheet with a linked table of contents, then a sheet per theme
(`themes` in `web/export/xlsx.go`; unlisted indicators go to "Ostatní") with one table per
indicator and breakdown (a row per period, a column per territory). Themes marked `compact`
(land use, one year per indicator) are a single table with a row per indicator. `/clenove/{item}`
lists the obce of a group or custom set (the "(?)" next to it).

The demo (`demo/build.py`) uses the same Charta checkout: serve the repository root
(`python -m http.server 8000`) and open `/demo/`.
