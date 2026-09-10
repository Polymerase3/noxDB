"""Unit tests for SQR/SQRP canonicalization (no DB required)."""

from __future__ import annotations

import pytest

from noxdb.samples import canonical_plate_id, plate_coords_from_name
from noxdb._import import schema


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("01", "01"),        # already canonical
        ("1", "01"),         # single digit zero-padded to the canonical width
        ("5", "05"),         # '5' and '05' must not describe two plates
        ("123", "123"),      # wider than the pad width → left alone
        (" 01 ", "01"),      # whitespace stripped
        ("", ""),            # empty → empty
        ("NA", ""),          # sentinel → canonical empty
        ("na", ""),
        ("N/A", ""),
        ("n/a", ""),
        (None, ""),
        ("Q1", "Q1"),        # non-numeric, non-sentinel → verbatim
        ("12", "12"),
    ],
)
def test_canonical_plate_id(raw, expected):
    assert canonical_plate_id(raw) == expected


def test_validate_plate_id_returns_canonical_and_no_warning_when_clean():
    canon, warn = schema.validate_plate_id("01", field="samples.csv row 2.sqr")
    assert canon == "01"
    assert warn is None


def test_validate_plate_id_warns_on_normalization():
    canon, warn = schema.validate_plate_id("NA", field="samples.csv row 2.sqrp")
    assert canon == ""
    assert warn is not None
    assert "normalized to ''" in warn


def test_validate_plate_id_whitespace_only_does_not_warn():
    """A pure whitespace difference is not worth a warning — the loader
    already strips cells, so it never reaches the DB un-stripped. Only
    the NA/empty sentinel collapse (a real semantic change) warns."""
    canon, warn = schema.validate_plate_id(" 07 ", field="samples.csv row 9.sqr")
    assert canon == "07"
    assert warn is None


def test_validate_plate_id_raises_when_too_long():
    with pytest.raises(ValueError, match="max is 10"):
        schema.validate_plate_id("ABCDEFGHIJK", field="samples.csv row 3.sqr")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("R42P02_09_PIC20_T1_A_T_C2", ("42", "02")),
        ("R05P01_1_0474408_KielP01_A_T_C2", ("05", "01")),
        ("R5P1_1_x_A_T_C2", ("05", "01")),          # unpadded name → canonical
        ("R31_input1_01_A_T_C2", ("31", "")),       # run-only: input series
        ("R02_input_01_A_T_C2", ("02", "")),
        ("no_coordinates_here", None),
        ("", None),
        (None, None),
    ],
)
def test_plate_coords_from_name(name, expected):
    assert plate_coords_from_name(name) == expected


def test_plate_coords_from_name_agrees_with_canonical_plate_id():
    """Both halves come back canonical, so they compare byte-for-byte."""
    sqr, sqrp = plate_coords_from_name("R5P1_7_x_A_T_C2")
    assert (sqr, sqrp) == (canonical_plate_id("5"), canonical_plate_id("1"))
