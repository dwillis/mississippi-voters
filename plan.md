# Mississippi Voter File CLI (`msvoters`)

## Context

The user receives two CSVs weekly from the Mississippi SOS: a full voter roll snapshot (~2.09M rows, 780MB) and a cumulative voting-history file (~26M rows, 2.2GB, suffixed `VotingHistory`). Two weeks are in `/Users/dwillis/code/miss-voters/` (7/27/2026 and 8/3/2026 drops). They want a Python CLI to ingest weekly pairs (oldest first), generate registration/party/geography reports, produce geographic subsets, and compare weeks over time.

**Decisions already made with the user:**
- Storage: **DuckDB** (single file, columnar, handles this scale trivially)
- Party is **derived from primary participation** in the history file (MS has no party registration; the voter file has no party, DOB, race, or gender columns — so "demographics" = geography + registration date + status)
- Output: terminal tables (rich) by default, `--csv` export; subsets always write CSV
- Ingest scans the folder and loads any un-ingested weekly pairs, oldest first
- Tooling: **uv** (never pip) — `uv run msvoters ...`

## Data facts (verified)

- Voter file header: `Mapping Value` (voter ID), name fields, address fields, `RES_CITY/RES_ZIP_CODE/RES_COUNTY`, `PRECINCT_CODE/NAME`, mailing fields, `EFFECTIVE_REGN_DATE`, `STATUS` (Active/Inactive), `DATE_VOTED` (last vote), then ~34 district columns (`CONG`, `SEN`, `REP`, `SUP`, `MUN`, `WARD`, `SCHD`, etc.). District/precinct values are space-padded → TRIM everything on ingest. Trailing comma in header = trailing empty column.
- History file header: `Mapping Value`, name fields, `ELECTION NAME`, `ELECTION TYPE` (General/Primary/Primary Runoff/General Runoff), `ELECTION DATE`, `POLITICAL PARTY` (populated only for primaries: Democrat/Republican/etc.)
- Filename pattern: `Weekly Distribution_kkirkpatrick_<M[M]D[D]YYYY>_94[VotingHistory].csv`. Parse date by stripping the 4-digit year, then splitting month/day (try both 1- and 2-digit month; if both valid, pick the one nearest the file's mtime). `--date MM/DD/YYYY` override flag as escape hatch.
- Dates in-file are `MM/DD/YYYY`.

## Project layout

```
miss-voters/
├── pyproject.toml          # uv project; deps: duckdb, click, rich
├── .gitignore              # add *.csv, *.duckdb (already ignores something — preserve)
├── msvoters/
│   ├── __init__.py
│   ├── cli.py              # click group + commands
│   ├── ingest.py           # file discovery, date parsing, loading
│   ├── db.py               # connection, schema DDL, migrations-lite
│   ├── reports.py          # totals / compare / party queries
│   └── subset.py           # geographic subset extraction
└── data/msvoters.duckdb    # created on first ingest (gitignored)
```

`pyproject.toml` declares `[project.scripts] msvoters = "msvoters.cli:main"` so everything runs as `uv run msvoters <command>`.

## Database schema (DuckDB)

- **`ingests`** — `snapshot_date DATE, kind TEXT ('voters'|'history'), filename, row_count, ingested_at`. Used to skip already-loaded files and to order snapshots.
- **`voters`** — all CSV columns (typed: `voter_id BIGINT`, `regn_date DATE`, `date_voted DATE`, everything else trimmed VARCHAR) **plus `snapshot_date DATE`**. One full snapshot per week; this is what enables over-time comparison and purge detection. ~2M rows/week.
- **`voting_history`** — latest cumulative file only (`voter_id, election_name, election_type, election_date DATE, party`, plus `snapshot_date` recording provenance). On each ingest of a newer history file: `DELETE` old rows, reload. Rationale: the file is cumulative, so stacking weekly copies costs ~2.2GB/week for near-zero information; purges are detected from voter snapshots instead.
- **`voter_party`** — materialized after each history load: per voter, `latest_primary_party`, `latest_primary_date`, `first_primary_party`, `dem_primaries`, `rep_primaries`, `other_primaries`, `switched BOOL` (voted in ≥2 different parties' primaries), `last_switch_date` (date of most recent primary whose party differs from the previous one). Built with one window-function query over primaries; ~26M → ~1.3M rows.

Loading uses DuckDB's native `read_csv` (`COPY`/`INSERT ... FROM read_csv(...)`) — no Python row loops; each file loads in seconds. Wrap each file's load in a transaction with the `ingests` row so a failed load doesn't half-register.

## CLI commands

```
uv run msvoters ingest [FOLDER] [--date MM/DD/YYYY] [--db PATH]
uv run msvoters snapshots
uv run msvoters totals [--snapshot DATE] [--csv DIR]
uv run msvoters compare [--from DATE] [--to DATE] [--csv DIR]
uv run msvoters party [--snapshot DATE] [--csv DIR]
uv run msvoters subset [filters] [-o FILE.csv]
```

- **`ingest`** — scan folder for filename pattern; pair voter/history files by date; skip pairs already in `ingests`; load remaining oldest-first; rebuild `voter_party` after the newest history load; print summary table (date, rows, new-vs-previous counts).
- **`snapshots`** — list ingested weeks with row counts.
- **`totals`** (defaults to latest snapshot; this is the "non-VotingHistory file" report) —
  - Registered voters total and by STATUS
  - **New registrations**: voter IDs present in this snapshot but not the previous one, plus voters with `EFFECTIVE_REGN_DATE` since the previous snapshot date (reported separately — they measure different things: roll additions vs. stated registration date)
  - New registrants by **county** (all 82, sorted), by **congressional district**, top cities/zips
  - New registrants by **derived party** (mostly "No primary history" — labeled honestly)
  - Registration trend: registrations by month for trailing 12 months (from `EFFECTIVE_REGN_DATE`)
  - With one snapshot only, the vs-previous sections print "first snapshot — no comparison" and date-based metrics still work.
- **`compare`** — between any two snapshots (default: two most recent): total/status deltas, **added voter IDs** (new registrations), **removed voter IDs** (purges) by county, Active↔Inactive movement, county-level net change table, derived-party mix shift.
- **`party`** — derived-party report: statewide breakdown (latest primary party), by county, **switchers**: count who ever voted both parties' primaries, and recent switchers (last_switch_date within N years, `--since` flag), sample-size caveats printed.
- **`subset`** — itemized voter rows to CSV. Filters (combinable): `--county`, `--city`, `--zip`, `--precinct`, `--cong` (e.g. USH02), `--senate`, `--house`, `--district COL=VALUE` (any district column), `--status`, `--registered-after/--registered-before`, `--party` (derived, joins `voter_party`), `--snapshot DATE`. Writes CSV with all voter columns + derived party; prints row count.

## Implementation steps

1. `uv init` the package, add deps (`uv add duckdb click rich`), set up `pyproject.toml` console script, extend `.gitignore` (`*.csv`, `*.duckdb`, `data/`).
2. `db.py`: schema DDL (idempotent `CREATE TABLE IF NOT EXISTS`), connection helper (`--db` default `data/msvoters.duckdb`).
3. `ingest.py`: filename discovery/date parsing (unit-testable pure function), pairing, DuckDB `read_csv` loads with TRIM + date casts, `voter_party` rebuild query.
4. `reports.py` + `subset.py` + `cli.py`: queries above, rich tables, CSV export via DuckDB `COPY (...) TO`.
5. Small test file for date parsing and the party-derivation SQL (against a tiny fixture CSV), run with `uv run pytest`.

## Verification

1. `uv run msvoters ingest .` — expect it to load 7/27 then 8/3 (4 files, ~2 min total); re-run and confirm it skips both.
2. `uv run msvoters snapshots` — two rows, correct counts (2,087,848 / 2,087,xxx voters).
3. `uv run msvoters totals` — sanity: statewide total ≈ 2.09M; counties = 82; spot-check one county's count with a direct `duckdb` query against the raw CSV.
4. `uv run msvoters compare` — added + removed between the two weeks; spot-check a few added IDs exist only in week 2's CSV (grep).
5. `uv run msvoters party` — breakdown sums to voters with ≥1 primary; spot-check one known switcher from raw history (e.g., a voter with both Democrat and Republican primary rows).
6. `uv run msvoters subset --county Hinds -o hinds.csv` — row count matches `totals` county figure.
