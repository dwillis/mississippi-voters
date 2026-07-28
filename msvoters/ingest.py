"""Weekly drop discovery, date parsing, and DuckDB loads for msvoters.

Files are named::

    Weekly Distribution_kkirkpatrick_<M[M]D[D]YYYY>_94.csv
    Weekly Distribution_kkirkpatrick_<M[M]D[D]YYYY>_94VotingHistory.csv

The date token packs month/day/year without separators: ``7272026`` = 7/27/2026,
``832026`` = 8/3/2026. Parsing strips the trailing 4-digit year, then splits the
remainder into month/day trying both 1- and 2-digit month widths; if both are
valid it falls back to the file's mtime to disambiguate.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

from . import db

# read_csv options that work around the SOS file's quirks:
#   - bare ``"`` characters inside unquoted name fields (e.g. ``"BETTY" S``) mean
#     the file is NOT RFC-4180 quoted, so quoting is disabled and ``,`` is the
#     only delimiter;
#   - the voter header lists an unpopulated ``IPD`` column + trailing empty, so
#     we read by position (header skipped) with an explicit column map;
#   - null_padding absorbs any ragged trailing columns.
READ_CSV_OPTIONS = (
    "header=false, skip=1, delim=',', quote='', escape='', "
    "null_padding=true, all_varchar=true"
)

_FILENAME_RE = re.compile(
    r"^Weekly Distribution_kkirkpatrick_(\d+)_94(VotingHistory)?\.csv$"
)

_HISTORY = "history"
_VOTERS = "voters"


@dataclass(frozen=True)
class DropFile:
    path: Path
    snapshot_date: _dt.date
    kind: str  # "voters" | "history"

    @property
    def filename(self) -> str:
        return self.path.name


# ---------------------------------------------------------------------------
# Date parsing (pure, unit-testable)
# ---------------------------------------------------------------------------

def parse_filename_date(filename: str, reference: _dt.date | None = None) -> _dt.date:
    """Parse the embedded date from an SOS filename.

    ``reference`` (defaults to the file's mtime at call sites) is used only to
    break ties when the packed date is ambiguous (both 1- and 2-digit month
    readings are valid calendar dates).
    """
    m = _FILENAME_RE.match(Path(filename).name)
    if not m:
        raise ValueError(f"not an SOS weekly file: {filename!r}")
    digits = m.group(1)
    if len(digits) < 5:  # need at least M + D + YYYY
        raise ValueError(f"date token too short in {filename!r}: {digits!r}")
    year = int(digits[-4:])
    rest = digits[:-4]
    if not (1 <= year <= 9999):
        raise ValueError(f"bad year in {filename!r}: {year}")

    candidates: list[_dt.date] = []
    for mlen in (1, 2):
        if len(rest) < mlen + 1:
            continue
        mo = int(rest[:mlen])
        day = int(rest[mlen:])
        if 1 <= mo <= 12 and 1 <= day <= 31:
            try:
                candidates.append(_dt.date(year, mo, day))
            except ValueError:
                continue

    if not candidates:
        raise ValueError(f"could not parse month/day from {filename!r}: {rest!r}")
    # de-dup (1- and 2-digit reads can yield the same date, e.g. month "03")
    uniq: list[_dt.date] = []
    for c in candidates:
        if c not in uniq:
            uniq.append(c)
    if len(uniq) == 1:
        return uniq[0]
    if reference is None:
        # Without a reference, prefer the 1-digit-month reading (matches how the
        # SOS packs single-digit months most commonly).
        return min(uniq, key=lambda d: (abs(d.month) if d.month < 10 else 1,))
    return min(uniq, key=lambda d: abs((d - reference).days))


# ---------------------------------------------------------------------------
# Discovery & pairing
# ---------------------------------------------------------------------------

def discover(folder: str | Path) -> list[DropFile]:
    """Find all SOS weekly files in *folder*, parsed and dated by mtime."""
    folder = Path(folder)
    drops: list[DropFile] = []
    for p in sorted(folder.iterdir()):
        m = _FILENAME_RE.match(p.name)
        if not m:
            continue
        kind = _HISTORY if m.group(2) == "VotingHistory" else _VOTERS
        ref = _dt.date.fromtimestamp(p.stat().st_mtime)
        date = parse_filename_date(p.name, reference=ref)
        drops.append(DropFile(path=p, snapshot_date=date, kind=kind))
    return drops


def pair_drops(drops: list[DropFile]) -> dict[_dt.date, dict[str, DropFile]]:
    """Group files by snapshot date into {'voters': ..., 'history': ...}."""
    pairs: dict[_dt.date, dict[str, DropFile]] = {}
    for d in drops:
        pairs.setdefault(d.snapshot_date, {"voters": None, "history": None})
        if d.kind in pairs[d.snapshot_date] and pairs[d.snapshot_date][d.kind] is not None:
            raise ValueError(f"duplicate {d.kind} file for {d.snapshot_date}: {d.filename}")
        pairs[d.snapshot_date][d.kind] = d
    return pairs


# ---------------------------------------------------------------------------
# Loads
# ---------------------------------------------------------------------------

def _voter_select_sql() -> str:
    cols = ", ".join(f"'{n}': 'VARCHAR'" for _s, n, _k in db.VOTER_COLUMNS)
    selects = []
    for _s, n, k in db.VOTER_COLUMNS:
        if k == "bigint":
            selects.append(f'TRY_CAST("{n}" AS BIGINT) AS "{n}"')
        elif k == "date":
            selects.append(f"TRY_STRPTIME(\"{n}\", '%m/%d/%Y')::DATE AS \"{n}\"")
        else:
            selects.append(f'TRIM("{n}") AS "{n}"')
    return cols, ", ".join(selects)


def load_voters(con, path: Path, snapshot_date: _dt.date) -> int:
    cols, select = _voter_select_sql()
    target_cols = ", ".join(['snapshot_date'] + [f'"{n}"' for n in db.VOTER_COLUMN_NAMES])
    sql = (
        f"INSERT INTO voters ({target_cols}) "
        f"SELECT DATE '{snapshot_date.isoformat()}' AS snapshot_date, {select} "
        f"FROM read_csv('{_sql_escape(path)}', columns={{{cols}}}, {READ_CSV_OPTIONS})"
    )
    con.execute(sql)
    return con.execute("SELECT COUNT(*) FROM voters WHERE snapshot_date = ?", [snapshot_date.isoformat()]).fetchone()[0]


def load_history(con, path: Path, snapshot_date: _dt.date) -> int:
    # 9 named columns + trailing empty column in the data.
    cols = (
        "'voter_id': 'VARCHAR', 'first_name': 'VARCHAR', 'middle_name': 'VARCHAR', "
        "'last_name': 'VARCHAR', 'suffix': 'VARCHAR', 'election_name': 'VARCHAR', "
        "'election_type': 'VARCHAR', 'election_date': 'VARCHAR', 'party': 'VARCHAR', "
        "'_trailing': 'VARCHAR'"
    )
    select = (
        "TRY_CAST(voter_id AS BIGINT) AS voter_id, "
        "TRIM(election_name) AS election_name, "
        "TRIM(election_type) AS election_type, "
        "TRY_STRPTIME(election_date, '%m/%d/%Y')::DATE AS election_date, "
        "TRIM(party) AS party"
    )
    con.execute("DELETE FROM voting_history")
    sql = (
        f"INSERT INTO voting_history (snapshot_date, voter_id, election_name, "
        f"election_type, election_date, party) "
        f"SELECT DATE '{snapshot_date.isoformat()}', {select} "
        f"FROM read_csv('{_sql_escape(path)}', columns={{{cols}}}, {READ_CSV_OPTIONS})"
    )
    con.execute(sql)
    return con.execute("SELECT COUNT(*) FROM voting_history").fetchone()[0]


def _sql_escape(path: Path) -> str:
    # Filenames here are simple, but guard against stray quotes/backslashes.
    return str(path).replace("'", "\\'")


# ---------------------------------------------------------------------------
# Derived party materialization
# ---------------------------------------------------------------------------

REBUILD_PARTY_SQL = """
DELETE FROM voter_party;
INSERT INTO voter_party (
    voter_id, latest_primary_party, latest_primary_date, first_primary_party,
    dem_primaries, rep_primaries, other_primaries, switched, last_switch_date
)
WITH p AS (
    SELECT voter_id, TRIM(party) AS party, election_date, election_name
    FROM voting_history
    WHERE election_type IN ('Primary', 'Primary Runoff')
      AND party IS NOT NULL AND TRIM(party) <> ''
),
seq AS (
    SELECT voter_id, party, election_date,
           ROW_NUMBER() OVER (
               PARTITION BY voter_id ORDER BY election_date, election_name
           ) AS rn,
           COUNT(*) OVER (PARTITION BY voter_id) AS n
    FROM p
),
agg AS (
    SELECT voter_id,
           ANY_VALUE(party) FILTER (WHERE rn = n) AS latest_primary_party,
           MAX(election_date) AS latest_primary_date,
           ANY_VALUE(party) FILTER (WHERE rn = 1) AS first_primary_party,
           COUNT(*) FILTER (WHERE party = 'Democrat') AS dem_primaries,
           COUNT(*) FILTER (WHERE party = 'Republican') AS rep_primaries,
           COUNT(*) FILTER (WHERE party NOT IN ('Democrat', 'Republican')) AS other_primaries,
           COUNT(DISTINCT party) AS distinct_parties
    FROM seq
    GROUP BY voter_id
),
sw AS (
    SELECT voter_id, MAX(election_date) AS last_switch_date
    FROM (
        SELECT voter_id, party, election_date,
               LAG(party) OVER (
                   PARTITION BY voter_id ORDER BY election_date, election_name
               ) AS prev_party
        FROM p
    ) WHERE prev_party IS NOT NULL AND party <> prev_party
    GROUP BY voter_id
)
SELECT a.voter_id, a.latest_primary_party, a.latest_primary_date, a.first_primary_party,
       a.dem_primaries, a.rep_primaries, a.other_primaries,
       a.distinct_parties > 1 AS switched, sw.last_switch_date
FROM agg a
LEFT JOIN sw USING (voter_id)
"""


def rebuild_voter_party(con) -> int:
    con.execute(REBUILD_PARTY_SQL)
    return con.execute("SELECT COUNT(*) FROM voter_party").fetchone()[0]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def already_ingested(con, snapshot_date: _dt.date) -> set[str]:
    rows = con.execute(
        "SELECT kind FROM ingests WHERE snapshot_date = ?",
        [snapshot_date.isoformat()],
    ).fetchall()
    return {r[0] for r in rows}


def ingest(folder: str | Path, db_path, date_override: _dt.date | None = None) -> dict:
    """Ingest all un-ingested weekly pairs in *folder*, oldest first.

    Returns a summary dict with per-date load counts.
    """
    con = db.connect(db_path)
    db.init_schema(con)
    try:
        pairs = pair_drops(discover(folder))

        # Apply a --date override (re-parses every file's date to that value).
        if date_override is not None:
            new_pairs = {}
            for d in pairs:
                new_pairs[date_override] = pairs[d]
            pairs = new_pairs

        ordered_dates = sorted(pairs)
        summary = {"dates": []}
        history_loaded = False
        for date in ordered_dates:
            pair = pairs[date]
            done = already_ingested(con, date)
            entry = {"date": date.isoformat(), "voters": None, "history": None, "skipped": sorted(done)}

            # Voters snapshot: always one row per week (never replaced).
            if "voters" not in done and pair["voters"] is not None:
                con.execute("BEGIN")
                try:
                    n = load_voters(con, pair["voters"].path, date)
                    con.execute(
                        "INSERT INTO ingests (snapshot_date, kind, filename, row_count) "
                        "VALUES (?, 'voters', ?, ?)",
                        [date.isoformat(), pair["voters"].filename, n],
                    )
                    con.execute("COMMIT")
                    entry["voters"] = n
                except Exception:
                    con.execute("ROLLBACK")
                    raise
            elif "voters" in done:
                n = con.execute(
                    "SELECT row_count FROM ingests WHERE snapshot_date=? AND kind='voters'",
                    [date.isoformat()],
                ).fetchone()[0]
                entry["voters"] = n

            # History: the voting_history table holds only the latest cumulative
            # file (DELETE + reload on each newer load), but every loaded file is
            # recorded in ingests for provenance and skip logic.
            if pair["history"] is not None and (date_override is not None or "history" not in done):
                con.execute("BEGIN")
                try:
                    n = load_history(con, pair["history"].path, date)
                    con.execute(
                        "INSERT INTO ingests (snapshot_date, kind, filename, row_count) "
                        "VALUES (?, 'history', ?, ?)",
                        [date.isoformat(), pair["history"].filename, n],
                    )
                    con.execute("COMMIT")
                    entry["history"] = n
                    history_loaded = True
                except Exception:
                    con.execute("ROLLBACK")
                    raise
            elif "history" in done:
                n = con.execute(
                    "SELECT row_count FROM ingests WHERE snapshot_date=? AND kind='history'",
                    [date.isoformat()],
                ).fetchone()[0]
                entry["history"] = n

            summary["dates"].append(entry)

        if history_loaded:
            summary["party_rows"] = rebuild_voter_party(con)

        return summary
    finally:
        con.close()