"""Build the static-site JSON (``docs/data``) from the DuckDB database.

Shapes the results of ``reports`` into three kinds of file the GitHub Pages
site fetches: ``latest.json`` (current week), ``history.json`` (all snapshots)
and ``counties/<slug>.json`` (one per county). ``events.json`` is hand-edited
and never overwritten.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from . import db, reports


def slugify(county: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", county.lower()).strip("-")


def _rows(section: reports.Section) -> list[dict]:
    return [dict(zip(section.columns, r)) for r in section.rows]


def _lag(dates: list[str], values: list[int], days: int) -> int | None:
    """Change vs. the snapshot closest to *days* before the latest one.

    Snapshots can be skipped, so this goes by calendar distance, not position.
    Returns None when history doesn't reach back that far (within 3 days).
    """
    if len(values) < 2:
        return None
    last = _dt.date.fromisoformat(dates[-1])
    target = last - _dt.timedelta(days=days)
    i = min(range(len(dates) - 1),
            key=lambda i: abs((_dt.date.fromisoformat(dates[i]) - target).days))
    if abs((_dt.date.fromisoformat(dates[i]) - target).days) > 3:
        return None
    return values[-1] - values[i]


def build(con, snapshot_date: _dt.date | None = None) -> dict:
    """Return {relative path: payload} for every file to write."""
    snapshot_date = snapshot_date or reports.resolve_snapshot(con, None)
    prev = reports.previous_snapshot(con, snapshot_date)
    hist = reports.history(con)
    hist["snapshots"] = [s for s in hist["snapshots"] if s["date"] <= snapshot_date.isoformat()]
    n_snaps = len(hist["snapshots"])
    for series in hist["counties"].values():
        del series[n_snaps:]

    totals = reports.totals(con, snapshot_date)
    by_title = {s.title.split(" — ")[0]: s for s in totals}
    cur = hist["snapshots"][-1]

    summary = reports.county_summary(con, snapshot_date, prev)
    for county, row in summary.items():
        series = hist["counties"].get(county, [])
        row["slug"] = slugify(county)
        dates = [x["date"] for x in hist["snapshots"]]
        row["delta_1wk"] = _lag(dates, series, 7)
        row["delta_4wk"] = _lag(dates, series, 28)
        row["delta_52wk"] = _lag(dates, series, 364)

    latest: dict = {
        "snapshot": snapshot_date.isoformat(),
        "previous": prev.isoformat() if prev else None,
        "days_since_previous": cur["days"],
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "totals": {"total": cur["total"], "active": cur["active"], "inactive": cur["inactive"]},
        "regn_trend_12mo": [[m, n] for m, n in
                            by_title["Registration trend"].rows],
        "counties": sorted(summary.values(), key=lambda r: r["county"]),
    }
    if prev is not None:
        cmp_ = {s.title.split(" — ")[0]: s for s in reports.compare(con, prev, snapshot_date)}
        latest["turnover"] = {r["measure"].split(" ")[0]: r["count"]
                              for r in _rows(cmp_["Roll turnover"])}
        latest["status_delta"] = _rows(cmp_["Status delta"])
        latest["status_movement"] = _rows(cmp_["Status movement (voters present in both snapshots)"])
        latest["party_mix"] = _rows(cmp_["Derived-party mix shift"])

    files: dict[str, object] = {"latest.json": latest, "history.json": hist}

    new_by = reports.county_new_registrants(con, snapshot_date, prev)
    trend = reports.county_regn_trend(con, snapshot_date)
    for county, row in summary.items():
        detail = {
            "snapshot": snapshot_date.isoformat(),
            "previous": latest["previous"],
            **row,
            "history": hist["counties"].get(county, []),
            "history_dates": [s["date"] for s in hist["snapshots"]],
            "new_registrants": new_by.get(county, {}),
            "regn_trend_12mo": trend.get(county, []),
        }
        files[f"counties/{row['slug']}.json"] = detail
    return files


def publish(db_path, out_dir: str | Path, snapshot_date: _dt.date | None = None) -> dict:
    out = Path(out_dir)
    con = db.connect(db_path)
    try:
        files = build(con, snapshot_date)
    finally:
        con.close()
    (out / "counties").mkdir(parents=True, exist_ok=True)
    # Drop county files for counties that no longer exist (renames, etc.).
    keep = {out / p for p in files}
    for stale in (out / "counties").glob("*.json"):
        if stale not in keep:
            stale.unlink()
    for rel, payload in files.items():
        with open(out / rel, "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
    return {"snapshot": files["latest.json"]["snapshot"], "files": len(files),
            "bytes": sum((out / p).stat().st_size for p in files)}
