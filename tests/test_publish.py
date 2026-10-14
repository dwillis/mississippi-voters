"""Tests for the static-site JSON built by ``msvoters publish``."""

import json

from msvoters import db
from msvoters.ingest import ingest
from msvoters.publish import publish, slugify

COLS = [n for _s, n, _k in db.VOTER_COLUMNS]


def _voters(rows):
    header = ",".join(src for src, _n, _k in db.VOTER_COLUMNS) + ",IPD,\n"
    out = [header]
    for vid, county, status in rows:
        r = [""] * len(COLS)
        r[COLS.index("voter_id")] = str(vid)
        r[COLS.index("res_county")] = county
        r[COLS.index("status")] = status
        r[COLS.index("cong")] = "USH01"
        r[COLS.index("res_city")] = "TUPELO"
        r[COLS.index("res_zip_code")] = "38801"
        out.append(",".join(r) + "\n")
    return "".join(out)


def _history(ids):
    head = "Mapping Value,FIRST,MIDDLE,LAST,SUFFIX,ELECTION,TYPE,DATE,PARTY,\n"
    return head + "".join(f"{i},A,,B,,2023 Primary,Primary,08/08/2023,Democrat,\n" for i in ids)


def _drop(folder, token, rows):
    (folder / f"Weekly Distribution_kkirkpatrick_{token}_94.csv").write_text(_voters(rows))
    (folder / f"Weekly Distribution_kkirkpatrick_{token}_94VotingHistory.csv").write_text(
        _history([r[0] for r in rows])
    )


def _build(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _drop(data, "7272026", [(1, "Hinds", "Active"), (2, "Hinds", "Active"), (3, "De Soto", "Active")])
    _drop(data, "832026", [(1, "Hinds", "Inactive"), (3, "De Soto", "Active"), (4, "De Soto", "Active")])
    dbp = tmp_path / "t.duckdb"
    ingest(data, dbp)
    out = tmp_path / "site"
    return publish(dbp, out), out


def test_slugify():
    assert slugify("De Soto") == "de-soto"
    assert slugify("St. Mary's") == "st-mary-s"


def test_publish_files_and_totals(tmp_path):
    res, out = _build(tmp_path)
    assert res["snapshot"] == "2026-08-03"
    latest = json.loads((out / "latest.json").read_text())
    assert latest["previous"] == "2026-07-27"
    assert latest["totals"] == {"total": 3, "active": 2, "inactive": 1}
    assert latest["turnover"] == {"added": 1, "removed": 1, "net": 0}
    assert [c["county"] for c in latest["counties"]] == ["De Soto", "Hinds"]
    desoto = latest["counties"][0]
    assert (desoto["total"], desoto["added"], desoto["removed"], desoto["net"]) == (2, 1, 0, 1)
    assert desoto["delta_1wk"] == 1 and desoto["delta_52wk"] is None
    assert {p.name for p in (out / "counties").glob("*.json")} == {"de-soto.json", "hinds.json"}


def test_history_and_county_detail(tmp_path):
    _res, out = _build(tmp_path)
    hist = json.loads((out / "history.json").read_text())
    assert [s["date"] for s in hist["snapshots"]] == ["2026-07-27", "2026-08-03"]
    assert hist["snapshots"][1]["net"] == 0 and hist["snapshots"][0]["net"] is None
    assert [s["days"] for s in hist["snapshots"]] == [None, 7]
    assert json.loads((out / "latest.json").read_text())["days_since_previous"] == 7
    assert hist["counties"] == {"Hinds": [2, 1], "De Soto": [1, 2]}
    d = json.loads((out / "counties" / "de-soto.json").read_text())
    assert d["history"] == [1, 2]
    assert d["new_registrants"]["by_city"] == [["TUPELO", 1]]
    assert d["new_registrants"]["by_party"] == [["Democrat", 1]]


def test_stale_county_files_removed(tmp_path):
    _res, out = _build(tmp_path)
    (out / "counties" / "gone.json").write_text("{}")
    publish(tmp_path / "t.duckdb", out)
    assert not (out / "counties" / "gone.json").exists()
