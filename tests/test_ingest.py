"""Tests for the SOS filename date parser."""

import datetime as dt

from msvoters.ingest import parse_filename_date


def test_single_digit_month_and_day():
    # 832026 -> rest "83" -> 8/3/2026
    assert parse_filename_date(
        "Weekly Distribution_kkirkpatrick_832026_94.csv"
    ) == dt.date(2026, 8, 3)


def test_two_digit_day():
    # 7272026 -> rest "727" -> 7/27/2026
    assert parse_filename_date(
        "Weekly Distribution_kkirkpatrick_7272026_94.csv"
    ) == dt.date(2026, 7, 27)


def test_history_suffix():
    assert parse_filename_date(
        "Weekly Distribution_kkirkpatrick_832026_94VotingHistory.csv"
    ) == dt.date(2026, 8, 3)


def test_ambiguous_picks_nearest_mtime():
    # 1222026 -> rest "122" -> 1/22/2026 or 12/2/2026
    near_dec = parse_filename_date(
        "Weekly Distribution_kkirkpatrick_1222026_94.csv", reference=dt.date(2026, 11, 15)
    )
    assert near_dec == dt.date(2026, 12, 2)
    near_jan = parse_filename_date(
        "Weekly Distribution_kkirkpatrick_1222026_94.csv", reference=dt.date(2026, 1, 20)
    )
    assert near_jan == dt.date(2026, 1, 22)


def test_two_digit_month_unambiguous():
    # 1132026 -> rest "113" -> 1/13/2026 (11/3 also valid? rest "113": m=11 d=3 -> Nov 3; m=1 d=13 -> Jan 13)
    # Both valid -> use reference tiebreak.
    jan13 = parse_filename_date(
        "Weekly Distribution_kkirkpatrick_1132026_94.csv", reference=dt.date(2026, 1, 1)
    )
    assert jan13 == dt.date(2026, 1, 13)


def test_rejects_nonmatching_filename():
    import pytest
    with pytest.raises(ValueError):
        parse_filename_date("some_other_file.csv")