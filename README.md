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

## Sample output

From the 2026-07-27 and 2026-08-03 drops (county tables trimmed to the top rows).

### `uv run msvoters totals`

```
 Registered voters — 2026-08-03 (total 2,088,191)
┏━━━━━━━━━━┳━━━━━━━━━━━┓
┃ status   ┃ count     ┃
┡━━━━━━━━━━╇━━━━━━━━━━━┩
│ Active   │ 1,852,831 │
│ Inactive │ 235,360   │
└──────────┴───────────┘

 Registered voters by county (all 82, sorted)
┏━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ county       ┃ count   ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Hinds        │ 169,795 │
│ Harrison     │ 160,829 │
│ DeSoto       │ 145,530 │
│ Rankin       │ 110,029 │
│ Jackson      │ 99,748  │
│ …            │ …       │  (76 more counties)
└──────────────┴─────────┘

 New registrations — vs. 2026-07-27
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ measure                                     ┃ count ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ added to roll (voter IDs new this snapshot) │ 1,356 │
│ regn_date since 2026-07-27 (stated)         │ 0     │
└─────────────────────────────────────────────┴───────┘
note: These measure different things: roll additions vs. stated registration date.

 New registrants by congressional district
┏━━━━━━━┳━━━━━━━┓
┃ cong  ┃ count ┃
┡━━━━━━━╇━━━━━━━┩
│ USH04 │ 429   │
│ USH03 │ 373   │
│ USH01 │ 289   │
│ USH02 │ 265   │
└───────┴───────┘

 New registrants by derived party
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ latest_primary_party ┃ count ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ No primary history   │ 1,208 │
│ Democrat             │ 76    │
│ Republican           │ 72    │
└──────────────────────┴───────┘

 Registration trend — trailing 12 months (by stated regn_date)
┏━━━━━━━━━━━━┳━━━━━━━┓
┃ month      ┃ count ┃
┡━━━━━━━━━━━━╇━━━━━━━┩
│ 2025-08-01 │ 5,766 │
│ 2025-09-01 │ 6,793 │
│ 2025-10-01 │ 6,824 │
│ 2025-11-01 │ 4,965 │
│ 2025-12-01 │ 5,375 │
│ 2026-01-01 │ 5,801 │
│ 2026-02-01 │ 7,082 │
│ 2026-03-01 │ 7,606 │
│ 2026-04-01 │ 6,143 │
│ 2026-05-01 │ 5,370 │
│ 2026-06-01 │ 6,447 │
│ 2026-07-01 │ 3,699 │
└────────────┴───────┘
```

### `uv run msvoters compare`

```
 Totals — 2026-07-27 -> 2026-08-03
┏━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ snapshot   ┃ voters    ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ 2026-07-27 │ 2,087,848 │
│ 2026-08-03 │ 2,088,191 │
│ net        │ 343       │
└────────────┴───────────┘

 Status delta
┏━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┓
┃ status   ┃ from      ┃ to        ┃ delta  ┃
┡━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━┩
│ Inactive │ 230,768   │ 235,360   │ 4,592  │
│ Active   │ 1,857,080 │ 1,852,831 │ -4,249 │
└──────────┴───────────┴───────────┴────────┘

 Roll turnover
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ measure                   ┃ count ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ added (new registrations) │ 1,356 │
│ removed (purges)          │ 1,013 │
│ net                       │ 343   │
└───────────────────────────┴───────┘

 County net change (added / removed / net)
┏━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━┳━━━━━┓
┃ county       ┃ added ┃ removed ┃ net ┃
┡━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━╇━━━━━┩
│ Jackson      │ 116   │ 3       │ 113 │
│ Harrison     │ 130   │ 35      │ 95  │
│ Forrest      │ 41    │ 7       │ 34  │
│ Rankin       │ 65    │ 35      │ 30  │
│ …            │ …     │ …       │ …   │  (78 more counties; net range −41 to +113)
└──────────────┴───────┴─────────┴─────┘

 Status movement (voters present in both snapshots)
┏━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┓
┃ from_status ┃ to_status ┃ count ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━┩
│ Active      │ Inactive  │ 5,357 │
│ Inactive    │ Active    │ 499   │
└─────────────┴───────────┴───────┘

 Derived-party mix shift
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┓
┃ latest_primary_party ┃ from    ┃ to      ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━┩
│ No primary history   │ 890,815 │ 891,010 │ 195   │
│ Republican           │ 665,157 │ 665,229 │ 72    │
│ Democrat             │ 531,681 │ 531,757 │ 76    │
│ All Other Parties    │ 194     │ 194     │ 0     │
│ Green                │ 1       │ 1       │ 0     │
└──────────────────────┴─────────┴─────────┴───────┘
```

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