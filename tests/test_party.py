"""Tests for the voter_party derivation SQL against a tiny fixture."""

import datetime as dt

import duckdb

from msvoters import db, ingest


def _build() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    db.init_schema(con)
    # Minimal voting_history fixture. Dates as ISO strings; DuckDB casts to DATE.
    rows = [
        # A: Dem 2018 -> Rep 2020 (a switcher, latest=Rep)
        (1, "Dem Primary 2018", "Primary", dt.date(2018, 6, 5), "Democrat"),
        (1, "Rep Primary 2020", "Primary", dt.date(2020, 3, 10), "Republican"),
        # B: only Rep 2020
        (2, "Rep Primary 2020", "Primary", dt.date(2020, 3, 10), "Republican"),
        # C: Dem 2016 + Dem 2020 (no switch)
        (3, "Dem Primary 2016", "Primary", dt.date(2016, 3, 1), "Democrat"),
        (3, "Dem Primary 2020", "Primary", dt.date(2020, 3, 10), "Democrat"),
        # D: Rep 2016 -> Dem 2018 -> Rep 2020 (two switches; latest switch 2020)
        (4, "Rep Primary 2016", "Primary", dt.date(2016, 3, 1), "Republican"),
        (4, "Dem Primary 2018", "Primary", dt.date(2018, 6, 5), "Democrat"),
        (4, "Rep Primary 2020", "Primary", dt.date(2020, 3, 10), "Republican"),
        # E: only a General (no primary) -> excluded from voter_party
        (5, "General 2020", "General", dt.date(2020, 11, 3), ""),
        # F: primary with "All Other Parties"
        (6, "Other Primary 2020", "Primary", dt.date(2020, 3, 10), "All Other Parties"),
        # G: a Primary Runoff counts as a primary
        (7, "Rep Runoff 2019", "Primary Runoff", dt.date(2019, 4, 1), "Republican"),
    ]
    con.executemany(
        "INSERT INTO voting_history "
        "(snapshot_date, voter_id, election_name, election_type, election_date, party) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(dt.date(2026, 8, 3),) + r for r in rows],
    )
    return con


def _party(con, voter_id):
    return con.execute(
        "SELECT latest_primary_party, latest_primary_date, first_primary_party, "
        "dem_primaries, rep_primaries, other_primaries, switched, last_switch_date "
        "FROM voter_party WHERE voter_id = ?",
        [voter_id],
    ).fetchone()


def test_rebuild_counts_and_switchers():
    con = _build()
    n = ingest.rebuild_voter_party(con)
    # A,B,C,D,F,G have primaries; E (general only) excluded.
    assert n == 6

    a = _party(con, 1)
    assert a[0] == "Republican"           # latest
    assert a[2] == "Democrat"              # first
    assert (a[3], a[4], a[5]) == (1, 1, 0)
    assert a[6] is True                    # switched
    assert a[7] == dt.date(2020, 3, 10)    # last switch date

    b = _party(con, 2)
    assert b[0] == "Republican"
    assert (b[3], b[4]) == (0, 1)
    assert b[6] is False
    assert b[7] is None

    c = _party(con, 3)
    assert c[3] == 2
    assert c[6] is False

    d = _party(con, 4)
    assert d[0] == "Republican"           # latest
    assert d[2] == "Republican"            # first
    assert (d[3], d[4]) == (1, 2)
    assert d[6] is True
    assert d[7] == dt.date(2020, 3, 10)   # most recent switch (Dem->Rep)

    e = _party(con, 5)
    assert e is None                       # no primaries -> not materialized

    f = _party(con, 6)
    assert f[5] == 1                       # other_primaries
    assert f[6] is False

    g = _party(con, 7)
    assert g[4] == 1                       # Primary Runoff counts
    assert g[0] == "Republican"
    con.close()


def test_rebuild_is_idempotent():
    con = _build()
    ingest.rebuild_voter_party(con)
    n2 = ingest.rebuild_voter_party(con)
    assert n2 == 6
    con.close()