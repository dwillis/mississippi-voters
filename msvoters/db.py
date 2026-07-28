"""DuckDB connection + schema for msvoters.

All tables are created idempotently with ``CREATE TABLE IF NOT EXISTS``.
The default database path is ``data/msvoters.duckdb`` (gitignored).
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

DEFAULT_DB = Path("data/msvoters.duckdb")

# ---------------------------------------------------------------------------
# Voter file column map.
#
# The SOS voter CSV has 59 named columns + a trailing empty column (the header
# ends with a comma). District/precinct values are space-padded, so every text
# column is TRIMmed on load. ``kind`` controls the cast:
#   "bigint"  -> Mapping Value (10-digit numeric voter ID)
#   "date"    -> EFFECTIVE_REGN_DATE / DATE_VOTED  (in-file format MM/DD/YYYY)
#   "varchar" -> everything else, trimmed
# ``source`` is the exact CSV header text; ``name`` is the snake-case DB column.
# ---------------------------------------------------------------------------
VOTER_COLUMNS: list[tuple[str, str, str]] = [
    ("Mapping Value", "voter_id", "bigint"),
    ("FIRST_NAME", "first_name", "varchar"),
    ("MIDDLE_NAME", "middle_name", "varchar"),
    ("LAST_NAME", "last_name", "varchar"),
    ("SUFFIX", "suffix", "varchar"),
    ("RESIDENTIAL_ADDRESS", "residential_address", "varchar"),
    ("HOUSE_NUM", "house_num", "varchar"),
    ("STREET_NAME", "street_name", "varchar"),
    ("STREET_TYPE", "street_type", "varchar"),
    ("PRE-DIRECTION", "pre_direction", "varchar"),
    ("POST-DIRECTION", "post_direction", "varchar"),
    ("RES_CITY", "res_city", "varchar"),
    ("RES_STATE", "res_state", "varchar"),
    ("RES_ZIP_CODE", "res_zip_code", "varchar"),
    ("RES_COUNTY", "res_county", "varchar"),
    ("PRECINCT_CODE", "precinct_code", "varchar"),
    ("PRECINCT_NAME", "precinct_name", "varchar"),
    ("MAILING_ADDRESS", "mailing_address", "varchar"),
    ("MAIL_CITY", "mail_city", "varchar"),
    ("MAIL_STATE", "mail_state", "varchar"),
    ("MAIL_ZIP_CODE", "mail_zip_code", "varchar"),
    ("EFFECTIVE_REGN_DATE", "regn_date", "date"),
    ("STATUS", "status", "varchar"),
    ("DATE_VOTED", "date_voted", "date"),
    ("US", "us", "varchar"),
    ("MS", "ms", "varchar"),
    ("CONG", "cong", "varchar"),
    ("SC", "sc", "varchar"),
    ("PSC", "psc", "varchar"),
    ("TC", "tc", "varchar"),
    ("DA", "da", "varchar"),
    ("SEN", "sen", "varchar"),
    ("REP", "rep", "varchar"),
    ("CA", "ca", "varchar"),
    ("CHC", "chc", "varchar"),
    ("CIR", "cir", "varchar"),
    ("CNT", "cnt", "varchar"),
    ("LEV", "lev", "varchar"),
    ("COCT", "coct", "varchar"),
    ("JUD", "jud", "varchar"),
    ("SCHC", "schc", "varchar"),
    ("SCIR", "scir", "varchar"),
    ("SUP", "sup", "varchar"),
    ("SUPR", "supr", "varchar"),
    ("EC", "ec", "varchar"),
    ("JC", "jc", "varchar"),
    ("JCJ", "jcj", "varchar"),
    ("CON", "con", "varchar"),
    ("MUN", "mun", "varchar"),
    ("WARD", "ward", "varchar"),
    ("WARDP", "wardp", "varchar"),
    ("MP", "mp", "varchar"),
    ("SCHD", "schd", "varchar"),
    ("SCHB", "schb", "varchar"),
    ("SBAL", "sbal", "varchar"),
    ("FIRE", "fire", "varchar"),
    ("FCD", "fcd", "varchar"),
    ("WSD", "wsd", "varchar"),
    # NOTE: the CSV header also lists "IPD" and has a trailing empty column, but
    # data rows only carry 58 fields (Mapping Value .. WSD). IPD is never
    # populated, so it is not loaded.
]

VOTER_COLUMN_NAMES = [name for _src, name, _kind in VOTER_COLUMNS]

# District columns accepted by ``msvoters subset --district COL=VALUE``.
DISTRICT_COLUMNS = {
    "us", "ms", "cong", "sc", "psc", "tc", "da", "sen", "rep", "ca", "chc",
    "cir", "cnt", "lev", "coct", "jud", "schc", "scir", "sup", "supr", "ec",
    "jc", "jcj", "con", "mun", "ward", "wardp", "mp", "schd", "schb", "sbal",
    "fire", "fcd", "wsd",
}


def connect(db_path: str | Path = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    """Open (creating parent dirs for) the DuckDB database file."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # read_only=False so the file is created on first use.
    return duckdb.connect(str(path))


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they don't already exist."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ingests (
            snapshot_date DATE    NOT NULL,
            kind          TEXT    NOT NULL,   -- 'voters' | 'history'
            filename      TEXT    NOT NULL,
            row_count     BIGINT,
            ingested_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (snapshot_date, kind)
        )
        """
    )

    # voters: one full snapshot per week, tagged with snapshot_date.
    voter_cols_sql = ",\n    ".join(
        f'"{name}" ' + ("BIGINT" if kind == "bigint" else "DATE" if kind == "date" else "VARCHAR")
        for _src, name, kind in VOTER_COLUMNS
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS voters (
            snapshot_date DATE NOT NULL,
            {voter_cols_sql}
        )
        """
    )
    # Speed up per-snapshot filtering and joins.
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_voters_snapshot ON voters (snapshot_date)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_voters_voterid ON voters (voter_id, snapshot_date)"
    )

    # voting_history: latest cumulative file only.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS voting_history (
            snapshot_date  DATE    NOT NULL,
            voter_id       BIGINT,
            election_name  VARCHAR,
            election_type  VARCHAR,
            election_date  DATE,
            party          VARCHAR
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_history_voterid ON voting_history (voter_id)"
    )

    # voter_party: derived from voting_history (primaries only).
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS voter_party (
            voter_id              BIGINT PRIMARY KEY,
            latest_primary_party  VARCHAR,
            latest_primary_date   DATE,
            first_primary_party   VARCHAR,
            dem_primaries          BIGINT,
            rep_primaries          BIGINT,
            other_primaries        BIGINT,
            switched               BOOLEAN,
            last_switch_date       DATE
        )
        """
    )


def resolve_db(db_path: str | None) -> Path:
    """Resolve a --db path, honoring the MSVOTERS_DB env var as a fallback."""
    if db_path:
        return Path(db_path)
    env = os.environ.get("MSVOTERS_DB")
    if env:
        return Path(env)
    return DEFAULT_DB