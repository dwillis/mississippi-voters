"""Geographic / attribute subset extraction for msvoters.

Builds a parameterized WHERE clause from the offered filters, optionally joins
``voter_party`` for the ``--party`` filter and the derived-party output column,
and writes the matching voter rows to CSV via DuckDB's ``COPY``.
"""

from __future__ import annotations

import datetime as _dt

from . import db


def build_filter_sql(
    *,
    county: str | None,
    city: str | None,
    zip: str | None,
    precinct: str | None,
    cong: str | None,
    senate: str | None,
    house: str | None,
    district: list[str] | None,   # list of "COL=VALUE"
    status: str | None,
    registered_after: _dt.date | None,
    registered_before: _dt.date | None,
    party: str | None,
    snapshot_date: _dt.date,
) -> tuple[str, list, bool]:
    """Return (where_sql, params, needs_party_join).

    Column names are whitelisted (never interpolated from raw input).
    """
    clauses: list[str] = ["v.snapshot_date = ?"]
    params: list = [snapshot_date]
    needs_party = party is not None

    def add(col: str, val: str | None) -> None:
        if val is None:
            return
        clauses.append(f"LOWER(v.{col}) = LOWER(?)")
        params.append(val)

    add("res_county", county)
    add("res_city", city)
    add("res_zip_code", zip)
    add("precinct_code", precinct)
    add("cong", cong)
    add("sen", senate)
    add("rep", house)
    add("status", status)

    if district:
        for spec in district:
            if "=" not in spec:
                raise SystemExit(f"--district expects COL=VALUE, got {spec!r}")
            col, val = spec.split("=", 1)
            col = col.strip().lower()
            if col not in db.DISTRICT_COLUMNS:
                raise SystemExit(
                    f"unknown district column {col!r}; valid: "
                    f"{', '.join(sorted(db.DISTRICT_COLUMNS))}"
                )
            clauses.append(f"LOWER(v.{col}) = LOWER(?)")
            params.append(val.strip())

    if registered_after is not None:
        clauses.append("v.regn_date >= ?")
        params.append(registered_after)
    if registered_before is not None:
        clauses.append("v.regn_date <= ?")
        params.append(registered_before)

    if party is not None:
        clauses.append("LOWER(p.latest_primary_party) = LOWER(?)")
        params.append(party)

    where = " AND ".join(clauses)
    return where, params, needs_party


def subset_query(
    con,
    out_csv: str | None,
    **filters,
) -> tuple[int, str | None]:
    """Run the subset. If *out_csv* is given, COPY to CSV and return (count, path).

    The output columns are all voter columns for the snapshot plus the derived
    ``latest_primary_party`` (NULL when the voter has no primary history).
    """
    where, params, needs_party = build_filter_sql(**filters)

    voter_cols = ", ".join(f'v."{n}"' for n in db.VOTER_COLUMN_NAMES)
    select_cols = ", ".join(db.VOTER_COLUMN_NAMES + ["latest_primary_party"])

    if needs_party:
        from_join = (
            "voters v LEFT JOIN voter_party p ON p.voter_id = v.voter_id"
        )
        select_expr = f"{voter_cols}, p.latest_primary_party"
    else:
        # Always expose derived party in the output, even when not filtering on it.
        from_join = (
            "voters v LEFT JOIN voter_party p ON p.voter_id = v.voter_id"
        )
        select_expr = f"{voter_cols}, p.latest_primary_party"

    sql = f"SELECT {select_expr} FROM {from_join} WHERE {where}"

    count = con.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]

    written = None
    if out_csv:
        # COPY ... TO with parameters via a prepared statement isn't supported for
        # the TO path; substitute the (safe, whitelisted) SQL into the COPY.
        con.execute(
            f"COPY ({sql}) TO '{out_csv.replace(chr(39), chr(39)+chr(39))}' "
            f"(HEADER, DELIMITER ',')",
            params,
        )
        written = out_csv

    return count, written