# msvoters

A CLI for the Mississippi Secretary of State weekly voter-file drops. Ingests
weekly CSV pairs (a full voter-roll snapshot + a cumulative voting-history file),
stores them in DuckDB, and produces registration / party / geography reports,
geographic subsets, and week-over-week comparisons.

MS has no party registration, so **party is derived from primary participation**
in the history file (the party a voter last voted a primary for).

## Setup

```bash
uv sync
```

Put the weekly drops (named `Weekly Distribution_kkirkpatrick_<M[M]D[D]YYYY>_94.csv`
and `...94VotingHistory.csv`) in a folder, then:

## Commands

```bash
uv run msvoters ingest [FOLDER] [--date MM/DD/YYYY]   # load un-ingested pairs, oldest first
uv run msvoters snapshots                             # list ingested weeks
uv run msvoters totals [--snapshot YYYY-MM-DD] [--csv DIR]
uv run msvoters compare [--from DATE] [--to DATE] [--csv DIR]
uv run msvoters party  [--snapshot DATE] [--since YEARS] [--csv DIR]
uv run msvoters subset [filters] -o FILE.csv
```

- `totals` defaults to the latest snapshot; reports registered totals by status,
  new registrants (roll additions vs. stated registration date) by county /
  congressional district / top cities / ZIPs / derived party, and a 12-month
  registration trend.
- `compare` (default: two most recent snapshots) reports status deltas, additions,
  purges, county net change, Active↔Inactive movement, and derived-party mix shift.
- `party` reports the statewide and county derived-party breakdown, ever-switchers,
  and (with `--since N`) recent switchers.
- `subset` writes matching voter rows (all columns + derived party) to CSV.
  Filters: `--county --city --zip --precinct --cong --senate --house
  --district COL=VALUE --status --registered-after --registered-before --party
  --snapshot`.

The DuckDB file defaults to `data/msvoters.duckdb` (override with `--db PATH`
or the `MSVOTERS_DB` env var).

## Tests

```bash
uv run pytest
```

## Data notes

- The voter CSV's header lists an `IPD` column and a trailing empty column that
  are **never populated** in the data rows, so 58 columns are loaded (by position,
  header skipped). District/precinct values are space-padded and trimmed on load.
- The file embeds bare `"` characters inside unquoted name fields (e.g.
  `"BETTY" S`), so it is not RFC-4180 quoted; CSV quoting is disabled and `,` is
  the sole delimiter.
- `EFFECTIVE_REGN_DATE` contains sentinel placeholders (e.g. `01/01/0001`); the
  12-month trend and `--registered-before` filters are not affected by them.
- `voters` keeps one full snapshot per week (enables over-time comparison and
  purge detection); `voting_history` keeps only the latest cumulative file.