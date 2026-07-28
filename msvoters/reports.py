"""Report queries for msvoters.

Each public function returns a list of :class:`Section` objects (title, column
headers, rows, optional note). ``cli.py`` renders these with rich and writes CSV
exports. Keeping the SQL here and the presentation in the CLI lets the same
queries back both the terminal table and the ``--csv`` export.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from . import db


@dataclass
class Section:
    title: str
    columns: list[str]
    rows: list[tuple]
    note: str | None = None


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def snapshot_dates(con) -> list[_dt.date]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT snapshot_date FROM voters ORDER BY 1"
    ).fetchall()]


def latest_snapshot(con) -> _dt.date | None:
    dates = snapshot_dates(con)
    return dates[-1] if dates else None


def previous_snapshot(con, date: _dt.date) -> _dt.date | None:
    rows = con.execute(
        "SELECT DISTINCT snapshot_date FROM voters WHERE snapshot_date < ? ORDER BY 1",
        [date],
    ).fetchall()
    return rows[-1][0] if rows else None


def resolve_snapshot(con, date_str: str | None) -> _dt.date:
    if date_str is None:
        latest = latest_snapshot(con)
        if latest is None:
            raise SystemExit("no snapshots ingested yet — run `msvoters ingest` first")
        return latest
    return _dt.date.fromisoformat(date_str)


def two_most_recent(con) -> tuple[_dt.date, _dt.date]:
    dates = snapshot_dates(con)
    if len(dates) < 2:
        raise SystemExit("need at least two snapshots to compare")
    return dates[-2], dates[-1]


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------

def snapshots(con) -> Section:
    rows = con.execute(
        """
        SELECT i.snapshot_date,
               v.voter_count     AS voters,
               i.row_count       AS history_rows
        FROM (
            SELECT snapshot_date, row_count
            FROM ingests WHERE kind = 'history'
        ) i
        JOIN (
            SELECT snapshot_date, COUNT(*) AS voter_count
            FROM voters GROUP BY snapshot_date
        ) v USING (snapshot_date)
        ORDER BY i.snapshot_date
        """
    ).fetchall()
    return Section(
        "Ingested snapshots",
        ["snapshot_date", "voters", "history_rows"],
        rows,
    )


# ---------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------

def totals(con, snapshot_date: _dt.date) -> list[Section]:
    prev = previous_snapshot(con, snapshot_date)
    out: list[Section] = []

    # 1. Registered total + by status.
    status_rows = con.execute(
        "SELECT status, COUNT(*) FROM voters WHERE snapshot_date=? GROUP BY status "
        "ORDER BY COUNT(*) DESC",
        [snapshot_date],
    ).fetchall()
    total = sum(r[1] for r in status_rows) if status_rows else 0
    out.append(Section(
        f"Registered voters — {snapshot_date.isoformat()} (total {total:,})",
        ["status", "count"],
        [(s or "(blank)", c) for s, c in status_rows],
    ))

    # 2. Registered voters by county (all 82, sorted).
    county_total_rows = con.execute(
        """
        SELECT res_county AS county, COUNT(*) AS n
        FROM voters
        WHERE snapshot_date = ? AND res_county IS NOT NULL
        GROUP BY res_county ORDER BY n DESC
        """,
        [snapshot_date],
    ).fetchall()
    out.append(Section(
        "Registered voters by county",
        ["county", "count"],
        [(c or "(blank)", n) for c, n in county_total_rows],
    ))

    # 3. New registrations vs. previous snapshot.
    if prev is None:
        out.append(Section(
            "New registrations vs. previous snapshot",
            ["measure", "count"],
            [],
            note="first snapshot — no comparison available",
        ))
    else:
        con.execute(
            "CREATE OR REPLACE TEMP TABLE tmp_new AS "
            "SELECT c.voter_id FROM voters c "
            "WHERE c.snapshot_date=? "
            "AND NOT EXISTS (SELECT 1 FROM voters p "
            "WHERE p.snapshot_date=? AND p.voter_id=c.voter_id)",
            [snapshot_date, prev],
        )
        added = con.execute("SELECT COUNT(*) FROM tmp_new").fetchone()[0]
        # Stated registration date since the previous snapshot date.
        stated = con.execute(
            "SELECT COUNT(*) FROM voters WHERE snapshot_date=? "
            "AND regn_date > ? AND EXTRACT(YEAR FROM regn_date) >= 1900",
            [snapshot_date, prev],
        ).fetchone()[0]
        out.append(Section(
            f"New registrations — vs. {prev.isoformat()}",
            ["measure", "count"],
            [
                ("added to roll (voter IDs new this snapshot)", added),
                (f"regn_date since {prev.isoformat()} (stated)", stated),
            ],
            note="These measure different things: roll additions vs. stated registration date.",
        ))

        # 3a. New registrants by county (all 82, sorted).
        county_rows = con.execute(
            """
            WITH counties AS (
                SELECT DISTINCT res_county AS county FROM voters
                WHERE snapshot_date=? AND res_county IS NOT NULL
            ),
            new AS (
                SELECT v.res_county AS county, COUNT(*) AS n
                FROM tmp_new t JOIN voters v
                  ON v.voter_id = t.voter_id AND v.snapshot_date = ?
                GROUP BY v.res_county
            )
            SELECT c.county, COALESCE(new.n, 0) AS n
            FROM counties c LEFT JOIN new USING (county)
            ORDER BY n DESC, c.county
            """,
            [snapshot_date, snapshot_date],
        ).fetchall()
        out.append(Section(
            "New registrants by county",
            ["county", "count"],
            [(c or "(blank)", n) for c, n in county_rows],
        ))

        # 3b. By congressional district.
        cong_rows = con.execute(
            """
            SELECT v.cong, COUNT(*) AS n
            FROM tmp_new t JOIN voters v
              ON v.voter_id = t.voter_id AND v.snapshot_date = ?
            GROUP BY v.cong ORDER BY n DESC
            """,
            [snapshot_date],
        ).fetchall()
        out.append(Section(
            "New registrants by congressional district",
            ["cong", "count"],
            [(c or "(blank)", n) for c, n in cong_rows],
        ))

        # 3c. Top cities / zips.
        city_rows = con.execute(
            """
            SELECT v.res_city, COUNT(*) AS n
            FROM tmp_new t JOIN voters v
              ON v.voter_id = t.voter_id AND v.snapshot_date = ?
            GROUP BY v.res_city ORDER BY n DESC LIMIT 15
            """,
            [snapshot_date],
        ).fetchall()
        out.append(Section(
            "New registrants — top cities (15)",
            ["city", "count"],
            [(c or "(blank)", n) for c, n in city_rows],
        ))
        zip_rows = con.execute(
            """
            SELECT v.res_zip_code, COUNT(*) AS n
            FROM tmp_new t JOIN voters v
              ON v.voter_id = t.voter_id AND v.snapshot_date = ?
            GROUP BY v.res_zip_code ORDER BY n DESC LIMIT 15
            """,
            [snapshot_date],
        ).fetchall()
        out.append(Section(
            "New registrants — top ZIPs (15)",
            ["zip", "count"],
            [(c or "(blank)", n) for c, n in zip_rows],
        ))

        # 3d. New registrants by derived party.
        party_rows = con.execute(
            """
            SELECT COALESCE(p.latest_primary_party, 'No primary history') AS party,
                   COUNT(*) AS n
            FROM tmp_new t
            JOIN voters v ON v.voter_id = t.voter_id AND v.snapshot_date = ?
            LEFT JOIN voter_party p ON p.voter_id = t.voter_id
            GROUP BY 1 ORDER BY n DESC
            """,
            [snapshot_date],
        ).fetchall()
        out.append(Section(
            "New registrants by derived party",
            ["latest_primary_party", "count"],
            party_rows,
            note="Party is derived from primary participation; most new registrants "
                 "have no primary history yet.",
        ))

    # 4. Registration trend — trailing 12 months by stated regn_date.
    trend_rows = con.execute(
        """
        SELECT CAST(DATE_TRUNC('month', regn_date) AS DATE) AS mo, COUNT(*) AS n
        FROM voters
        WHERE snapshot_date = ?
          AND regn_date <= ?
          AND regn_date >= (?::DATE - INTERVAL '12 months')
          AND EXTRACT(YEAR FROM regn_date) >= 1900
        GROUP BY mo ORDER BY mo
        """,
        [snapshot_date, snapshot_date, snapshot_date],
    ).fetchall()
    out.append(Section(
        "Registration trend — trailing 12 months (by stated regn_date)",
        ["month", "count"],
        [(mo.isoformat(), n) for mo, n in trend_rows],
    ))

    return out


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

def compare(con, from_date: _dt.date, to_date: _dt.date) -> list[Section]:
    out: list[Section] = []

    # 1. Totals.
    f_tot = con.execute("SELECT COUNT(*) FROM voters WHERE snapshot_date=?", [from_date]).fetchone()[0]
    t_tot = con.execute("SELECT COUNT(*) FROM voters WHERE snapshot_date=?", [to_date]).fetchone()[0]
    out.append(Section(
        f"Totals — {from_date.isoformat()} -> {to_date.isoformat()}",
        ["snapshot", "voters"],
        [(from_date.isoformat(), f_tot), (to_date.isoformat(), t_tot),
         ("net", t_tot - f_tot)],
    ))

    # 2. Status delta.
    status_rows = con.execute(
        """
        WITH f AS (SELECT status, COUNT(*) n FROM voters WHERE snapshot_date=? GROUP BY status),
             t AS (SELECT status, COUNT(*) n FROM voters WHERE snapshot_date=? GROUP BY status)
        SELECT COALESCE(f.status, t.status) AS status,
               COALESCE(f.n, 0) AS frm, COALESCE(t.n, 0) AS to, COALESCE(t.n,0)-COALESCE(f.n,0) AS delta
        FROM f FULL OUTER JOIN t USING (status)
        ORDER BY delta DESC
        """,
        [from_date, to_date],
    ).fetchall()
    out.append(Section(
        "Status delta",
        ["status", "from", "to", "delta"],
        [(s or "(blank)", f, t, d) for s, f, t, d in status_rows],
    ))

    # 3. Added / removed totals.
    con.execute(
        "CREATE OR REPLACE TEMP TABLE tmp_added AS "
        "SELECT t.voter_id FROM voters t WHERE t.snapshot_date=? "
        "AND NOT EXISTS (SELECT 1 FROM voters f WHERE f.snapshot_date=? AND f.voter_id=t.voter_id)",
        [to_date, from_date],
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE tmp_removed AS "
        "SELECT f.voter_id FROM voters f WHERE f.snapshot_date=? "
        "AND NOT EXISTS (SELECT 1 FROM voters t WHERE t.snapshot_date=? AND t.voter_id=f.voter_id)",
        [from_date, to_date],
    )
    added = con.execute("SELECT COUNT(*) FROM tmp_added").fetchone()[0]
    removed = con.execute("SELECT COUNT(*) FROM tmp_removed").fetchone()[0]
    out.append(Section(
        "Roll turnover",
        ["measure", "count"],
        [("added (new registrations)", added), ("removed (purges)", removed),
         ("net", added - removed)],
    ))

    # 4. County-level net change (all counties present in either snapshot).
    county_rows = con.execute(
        """
        WITH counties AS (
            SELECT DISTINCT res_county AS county FROM voters
            WHERE snapshot_date IN (?, ?) AND res_county IS NOT NULL
        ),
        a AS (SELECT v.res_county AS county, COUNT(*) AS n
               FROM tmp_added x JOIN voters v
                 ON v.voter_id=x.voter_id AND v.snapshot_date=?
               GROUP BY v.res_county),
        r AS (SELECT v.res_county AS county, COUNT(*) AS n
               FROM tmp_removed x JOIN voters v
                 ON v.voter_id=x.voter_id AND v.snapshot_date=?
               GROUP BY v.res_county)
        SELECT c.county,
               COALESCE(a.n, 0) AS added, COALESCE(r.n, 0) AS removed,
               COALESCE(a.n, 0) - COALESCE(r.n, 0) AS net
        FROM counties c
        LEFT JOIN a USING (county)
        LEFT JOIN r USING (county)
        ORDER BY net DESC, c.county
        """,
        [from_date, to_date, to_date, from_date],
    ).fetchall()
    out.append(Section(
        "County net change (added / removed / net)",
        ["county", "added", "removed", "net"],
        [(c or "(blank)", a, r, n) for c, a, r, n in county_rows],
    ))

    # 5. Active <-> Inactive movement (voters present in both with a status change).
    move_rows = con.execute(
        """
        SELECT f.status AS from_status, t.status AS to_status, COUNT(*) AS n
        FROM voters f JOIN voters t ON f.voter_id = t.voter_id
        WHERE f.snapshot_date=? AND t.snapshot_date=?
          AND f.status <> t.status
        GROUP BY f.status, t.status ORDER BY n DESC
        """,
        [from_date, to_date],
    ).fetchall()
    out.append(Section(
        "Status movement (voters present in both snapshots)",
        ["from_status", "to_status", "count"],
        [(f or "(blank)", t or "(blank)", n) for f, t, n in move_rows],
    ))

    # 6. Derived-party mix shift (latest_primary_party) for each snapshot's roll.
    party_rows = con.execute(
        """
        WITH f AS (SELECT COALESCE(p.latest_primary_party,'No primary history') party, COUNT(*) n
                   FROM voters v LEFT JOIN voter_party p ON p.voter_id=v.voter_id
                   WHERE v.snapshot_date=? GROUP BY 1),
             t AS (SELECT COALESCE(p.latest_primary_party,'No primary history') party, COUNT(*) n
                   FROM voters v LEFT JOIN voter_party p ON p.voter_id=v.voter_id
                   WHERE v.snapshot_date=? GROUP BY 1)
        SELECT COALESCE(f.party, t.party) AS party,
               COALESCE(f.n,0) AS frm, COALESCE(t.n,0) AS to, COALESCE(t.n,0)-COALESCE(f.n,0) AS delta
        FROM f FULL OUTER JOIN t USING (party)
        ORDER BY COALESCE(t.n,0) DESC
        """,
        [from_date, to_date],
    ).fetchall()
    out.append(Section(
        "Derived-party mix shift",
        ["latest_primary_party", "from", "to", "delta"],
        party_rows,
        note="Party is derived from primary participation (voter_party materialized from the "
             "latest cumulative history file).",
    ))

    return out


# ---------------------------------------------------------------------------
# party
# ---------------------------------------------------------------------------

def party_report(con, snapshot_date: _dt.date, since_years: int | None) -> list[Section]:
    out: list[Section] = []

    # Statewide breakdown of latest primary party among registered voters.
    state_rows = con.execute(
        """
        SELECT COALESCE(p.latest_primary_party, 'No primary history') AS party,
               COUNT(*) AS n
        FROM voters v LEFT JOIN voter_party p ON p.voter_id = v.voter_id
        WHERE v.snapshot_date = ?
        GROUP BY 1 ORDER BY n DESC
        """,
        [snapshot_date],
    ).fetchall()
    with_history = sum(n for p, n in state_rows if p != "No primary history")
    out.append(Section(
        f"Derived party — statewide ({snapshot_date.isoformat()})",
        ["latest_primary_party", "count"],
        state_rows,
        note=f"{with_history:,} voters have ≥1 primary; party sums to that. "
             "Party = party of most recent primary voted (Democrat/Republican/other).",
    ))

    # By county: latest-primary-party counts. Aliases are suffixed to avoid
    # colliding with voter columns (e.g. ``rep`` is a district column).
    county_rows = con.execute(
        """
        SELECT v.res_county AS county,
               COUNT(*) FILTER (WHERE p.latest_primary_party='Democrat') AS dem_n,
               COUNT(*) FILTER (WHERE p.latest_primary_party='Republican') AS rep_n,
               COUNT(*) FILTER (WHERE p.latest_primary_party IS NOT NULL
                                AND p.latest_primary_party NOT IN ('Democrat','Republican')) AS oth_n,
               COUNT(*) FILTER (WHERE p.latest_primary_party IS NULL) AS none_n
        FROM voters v LEFT JOIN voter_party p ON p.voter_id = v.voter_id
        WHERE v.snapshot_date = ?
        GROUP BY v.res_county ORDER BY (dem_n + rep_n) DESC
        """,
        [snapshot_date],
    ).fetchall()
    out.append(Section(
        "Derived party by county (latest primary)",
        ["county", "dem", "rep", "other", "no_history"],
        [(c or "(blank)", d, r, o, nh) for c, d, r, o, nh in county_rows],
    ))

    # Switchers.
    switch_rows = con.execute(
        """
        SELECT
          COUNT(*) AS total_with_primaries,
          COUNT(*) FILTER (WHERE switched) AS ever_switched,
          COUNT(*) FILTER (WHERE switched AND last_switch_date IS NOT NULL) AS switched_with_date
        FROM voter_party
        """,
    ).fetchall()[0]
    out.append(Section(
        "Primary-party switchers",
        ["measure", "count"],
        [
            ("voters with ≥1 primary", switch_rows[0]),
            ("ever voted ≥2 parties' primaries", switch_rows[1]),
            ("of those, with a known switch date", switch_rows[2]),
        ],
        note="A switcher has voted primaries of two different parties at any time.",
    ))

    if since_years is not None:
        cutoff = _dt.date(snapshot_date.year - since_years, snapshot_date.month, snapshot_date.day)
        recent = con.execute(
            """
            SELECT latest_primary_party AS party, COUNT(*) AS n
            FROM voter_party
            WHERE switched AND last_switch_date >= ?
            GROUP BY 1 ORDER BY n DESC
            """,
            [cutoff],
        ).fetchall()
        out.append(Section(
            f"Recent switchers — last_switch_date since {cutoff.isoformat()} (≈{since_years}y)",
            ["latest_primary_party", "count"],
            recent,
            note="Sample is small; treat percentages cautiously.",
        ))

    return out