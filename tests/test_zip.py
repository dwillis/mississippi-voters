"""Tests for discovering and ingesting SOS drops packed in .zip archives."""

import datetime as dt
import zipfile

import duckdb

from msvoters import db
from msvoters.ingest import discover, ingest, materialize

VOTERS = "Weekly Distribution_kkirkpatrick_832026_94.csv"
HISTORY = "Weekly Distribution_kkirkpatrick_832026_94VotingHistory.csv"


def _voter_csv() -> str:
    header = ",".join(src for src, _n, _k in db.VOTER_COLUMNS) + ",IPD,\n"
    row = ["1234567890", "JANE"] + [""] * (len(db.VOTER_COLUMNS) - 2)
    return header + ",".join(row) + "\n"


def _history_csv() -> str:
    return (
        "Mapping Value,FIRST,MIDDLE,LAST,SUFFIX,ELECTION,TYPE,DATE,PARTY,\n"
        "1234567890,JANE,,DOE,,2023 Primary,Primary,08/08/2023,Democrat,\n"
    )


def _make_zip(path, members: dict[str, str]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)


def test_discover_finds_zip_members(tmp_path):
    _make_zip(tmp_path / "drop.zip", {f"sub/{VOTERS}": "x", HISTORY: "y", "readme.txt": "z"})
    drops = sorted(discover(tmp_path), key=lambda d: d.kind)
    assert [(d.kind, d.filename, d.snapshot_date) for d in drops] == [
        ("history", HISTORY, dt.date(2026, 8, 3)),
        ("voters", VOTERS, dt.date(2026, 8, 3)),
    ]
    assert all(d.path.name == "drop.zip" for d in drops)


def test_loose_csv_wins_over_zip_member(tmp_path):
    (tmp_path / VOTERS).write_text("loose")
    _make_zip(tmp_path / "drop.zip", {VOTERS: "zipped"})
    (d,) = discover(tmp_path)
    assert d.member is None


def test_materialize_extracts_and_cleans_up(tmp_path):
    _make_zip(tmp_path / "drop.zip", {f"../evil/{VOTERS}": "payload"})
    (d,) = discover(tmp_path)
    with materialize(d) as p:
        assert p.read_text() == "payload"
        assert p.parent.parent == tmp_path
    assert not p.exists()
    assert sorted(x.name for x in tmp_path.iterdir()) == ["drop.zip"]


def test_ingest_from_zip(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _make_zip(data / "drop.zip", {VOTERS: _voter_csv(), HISTORY: _history_csv()})
    dbp = tmp_path / "t.duckdb"
    summary = ingest(data, dbp)
    assert summary["dates"][0]["voters"] == 1
    assert summary["dates"][0]["history"] == 1
    con = duckdb.connect(str(dbp))
    assert con.execute("SELECT latest_primary_party FROM voter_party").fetchone() == ("Democrat",)
    assert sorted(x.name for x in data.iterdir()) == ["drop.zip"]
