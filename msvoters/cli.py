"""Command-line entry point for msvoters.

Run as ``uv run msvoters <command>`` (the console script in pyproject.toml).
"""

from __future__ import annotations

import csv
import datetime as _dt
import re
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import db, ingest, publish as publish_mod, reports, subset

console = Console()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return s or "section"


def _fmt_cell(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, _dt.date):
        return v.isoformat()
    if v is None:
        return ""
    return str(v)


def render_sections(sections: list[reports.Section]) -> None:
    for sec in sections:
        table = Table(title=sec.title, title_style="bold", show_lines=False)
        for col in sec.columns:
            table.add_column(col, overflow="fold")
        for row in sec.rows:
            table.add_row(*[_fmt_cell(c) for c in row])
        console.print(table)
        if sec.note:
            console.print(f"[dim italic]note: {sec.note}[/dim italic]")
        console.print()


def export_sections(sections: list[reports.Section], csv_dir: str, prefix: str) -> None:
    out = Path(csv_dir)
    out.mkdir(parents=True, exist_ok=True)
    for sec in sections:
        path = out / f"{prefix}_{_slug(sec.title)}.csv"
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(sec.columns)
            for row in sec.rows:
                w.writerow([("" if c is None else c) for c in row])
        console.print(f"[dim]wrote {path} ({len(sec.rows)} rows)[/dim]")


def _parse_date(s: str | None) -> _dt.date | None:
    if s is None:
        return None
    return _dt.date.fromisoformat(s)


def _parse_snapshot_override(s: str | None) -> _dt.date | None:
    if s is None:
        return None
    if "-" in s:
        return _dt.date.fromisoformat(s)
    return _dt.datetime.strptime(s, "%m/%d/%Y").date()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
def main() -> None:
    """Mississippi voter file CLI — ingest, report, compare, subset."""


@main.command(name="ingest")
@click.argument("folder", default="data")
@click.option("--date", "date_str", default=None,
              help="Override snapshot date (MM/DD/YYYY or YYYY-MM-DD).")
@click.option("--db", "db_path", default=None, help="DuckDB path (default data/msvoters.duckdb).")
def ingest_cmd(folder, date_str, db_path):
    """Ingest un-ingested weekly SOS drops in FOLDER (default data/), oldest first.

    CSVs can be loose or inside .zip files; zips are extracted only as needed.
    """
    summary = ingest_impl(folder, date_str, db_path)  # noqa: F811
    table = Table(title="Ingest summary")
    table.add_column("date")
    table.add_column("voters", justify="right")
    table.add_column("history", justify="right")
    table.add_column("skipped")
    for e in summary["dates"]:
        table.add_row(
            e["date"],
            _fmt_cell(e["voters"]),
            _fmt_cell(e["history"]),
            ", ".join(e["skipped"]) or "-",
        )
    console.print(table)
    if "party_rows" in summary:
        console.print(f"[dim]rebuilt voter_party: {summary['party_rows']:,} rows[/dim]")


def ingest_impl(folder, date_str, db_path):
    return ingest.ingest(folder, db.resolve_db(db_path), _parse_snapshot_override(date_str))


@main.command(name="publish")
@click.option("--out", "out_dir", default="docs/data", show_default=True,
              help="Output directory for the site's JSON files.")
@click.option("--snapshot", "snapshot", default=None, help="Snapshot date (default: latest).")
@click.option("--db", "db_path", default=None)
def publish_cmd(out_dir, snapshot, db_path):
    """Write the static-site JSON (latest, history, per-county) to --out."""
    res = publish_mod.publish(db.resolve_db(db_path), out_dir, _parse_date(snapshot))
    console.print(
        f"published snapshot {res['snapshot']}: {res['files']} files, "
        f"{res['bytes'] / 1024:.0f} KiB -> {out_dir}"
    )


@main.command()
@click.option("--db", "db_path", default=None)
def snapshots(db_path):
    """List ingested weeks with row counts."""
    con = db.connect(db.resolve_db(db_path))
    try:
        render_sections([reports.snapshots(con)])
    finally:
        con.close()


@main.command()
@click.option("--snapshot", "snapshot_str", default=None,
              help="Snapshot date YYYY-MM-DD (default: latest).")
@click.option("--csv", "csv_dir", default=None, help="Directory to write CSV exports.")
@click.option("--db", "db_path", default=None)
def totals(snapshot_str, csv_dir, db_path):
    """Registration totals, new registrants, and registration trend."""
    con = db.connect(db.resolve_db(db_path))
    try:
        snap = reports.resolve_snapshot(con, snapshot_str)
        sections = reports.totals(con, snap)
        render_sections(sections)
        if csv_dir:
            export_sections(sections, csv_dir, "totals")
    finally:
        con.close()


@main.command()
@click.option("--from", "from_str", default=None, help="From snapshot date (default: 2nd-most-recent).")
@click.option("--to", "to_str", default=None, help="To snapshot date (default: most recent).")
@click.option("--csv", "csv_dir", default=None, help="Directory to write CSV exports.")
@click.option("--db", "db_path", default=None)
def compare(from_str, to_str, csv_dir, db_path):
    """Compare two snapshots: deltas, additions, purges, movement."""
    con = db.connect(db.resolve_db(db_path))
    try:
        if from_str and to_str:
            f = _dt.date.fromisoformat(from_str)
            t = _dt.date.fromisoformat(to_str)
        else:
            f, t = reports.two_most_recent(con)
        sections = reports.compare(con, f, t)
        render_sections(sections)
        if csv_dir:
            export_sections(sections, csv_dir, "compare")
    finally:
        con.close()


@main.command()
@click.option("--snapshot", "snapshot_str", default=None, help="Snapshot date (default: latest).")
@click.option("--since", "since_years", type=int, default=None,
              help="Recent-switcher window in years.")
@click.option("--csv", "csv_dir", default=None, help="Directory to write CSV exports.")
@click.option("--db", "db_path", default=None)
def party(snapshot_str, since_years, csv_dir, db_path):
    """Derived-party report (from primary participation)."""
    con = db.connect(db.resolve_db(db_path))
    try:
        snap = reports.resolve_snapshot(con, snapshot_str)
        sections = reports.party_report(con, snap, since_years)
        render_sections(sections)
        if csv_dir:
            export_sections(sections, csv_dir, "party")
    finally:
        con.close()


@main.command(name="subset")
@click.option("--county")
@click.option("--city")
@click.option("--zip")
@click.option("--precinct", help="Precinct code.")
@click.option("--cong", help="Congressional district, e.g. USH02.")
@click.option("--senate", help="State senate district (SEN column).")
@click.option("--house", help="State house district (REP column).")
@click.option("--district", "district", multiple=True,
              help="Any district column: COL=VALUE (repeatable).")
@click.option("--status")
@click.option("--registered-after", "reg_after", default=None, help="YYYY-MM-DD.")
@click.option("--registered-before", "reg_before", default=None, help="YYYY-MM-DD.")
@click.option("--party", default=None,
              help="Derived latest primary party (Democrat/Republican/...).")
@click.option("--snapshot", "snapshot_str", default=None, help="Snapshot date (default: latest).")
@click.option("-o", "--out", "out_csv", required=True, help="Output CSV path.")
@click.option("--db", "db_path", default=None)
def subset_cmd(county, city, zip, precinct, cong, senate, house, district, status,
           reg_after, reg_before, party, snapshot_str, out_csv, db_path):
    """Extract matching voter rows to CSV (all voter columns + derived party)."""
    con = db.connect(db.resolve_db(db_path))
    try:
        snap = reports.resolve_snapshot(con, snapshot_str)
        count, written = subset.subset_query(
            con,
            out_csv=out_csv,
            county=county, city=city, zip=zip, precinct=precinct, cong=cong,
            senate=senate, house=house, district=list(district), status=status,
            registered_after=_parse_date(reg_after),
            registered_before=_parse_date(reg_before),
            party=party, snapshot_date=snap,
        )
        console.print(f"[bold green]{count:,}[/bold green] rows -> {written}")
    finally:
        con.close()


if __name__ == "__main__":
    main()