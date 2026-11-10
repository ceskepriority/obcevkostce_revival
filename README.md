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
