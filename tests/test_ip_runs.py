"""Unit tests for reading IP coordinates from the IP runs sheet (no DB required)."""

from __future__ import annotations

import pytest

from noxdb.ip_runs import by_old_label, coords_for_old_name, load_plates

# Three header rows as Excel exports them; N (run) is filled on the
# first plate of each run only, P holds the old label where it differs.
_HEADER = (
    "Run number;Ab type;Short plate info (original plate info);;;;;;;;;;;Naming;;;\n"
    ";;;;;;;;;;;;;IP run #;Plate #;Combined*\n"
    ";;;Library;Batch;;;;;;;;;;;\n"
)


def _sheet(tmp_path, body: str):
    path = tmp_path / "overview.csv"
    path.write_text(_HEADER + body, encoding="cp1252")
    return path


@pytest.fixture
def plates(tmp_path):
    return load_plates(_sheet(tmp_path, (
        "IPR02;IgA;IgA test plate;;;;;;;;;;;2;1;\n"
        "IPR04;IgG;CORSA 1;;;;;;;;;;;4;03;R02P01\n"
        ";;CORSA 2;;;;;;;;;;;;04;R02P02\n"
        "IPR08;IgG;PCa Innsbruck P1;;;;;;;;;;;8;01;R08P01\n"
        ";;Arno PREDICTS P5 – µl;;;;;;;;;;;;04;R08P01\n"
        ";;Total samples sum:;;;;;;;;;;;;;\n"
    )))


def test_run_is_carried_down_and_canonical(plates):
    assert [p.label for p in plates] == ["R02P01", "R04P03", "R04P04", "R08P01", "R08P04"]


def test_old_label_is_kept(plates):
    assert plates[2].old_label == "R02P02"
    assert plates[0].old_label is None


def test_old_label_translates_to_the_sheet_plate(plates):
    index = by_old_label(plates)
    assert coords_for_old_name(index, "R02P02_01_CORSAp2_4153_A_T_C2") == [("04", "04")]


def test_clash_is_settled_by_project(plates):
    index = by_old_label(plates)
    name = "R08P01_01_x_A_T_C2"
    assert len(coords_for_old_name(index, name)) == 2
    assert coords_for_old_name(index, name, {"PREDICTS"}) == [("08", "04")]
    assert coords_for_old_name(index, name, {"PCa_Innsbruck"}) == [("08", "01")]


def test_true_label_clashing_with_an_old_one(plates):
    """R02P01 is both the IgA test plate and CORSA 1's old label."""
    index = by_old_label(plates)
    assert coords_for_old_name(index, "R02P01_05_x", {"CORSA"}) == [("04", "03")]


def test_run_only_name(plates):
    index = by_old_label(plates)
    assert coords_for_old_name(index, "R08_input_01_A_T_C2") == [("08", "")]
    assert coords_for_old_name(index, "R02_input_01_A_T_C2") == [("02", ""), ("04", "")]


def test_unknown_names(plates):
    index = by_old_label(plates)
    assert coords_for_old_name(index, "R77P01_01_x") == []
    assert coords_for_old_name(index, "no_coordinates_here") == []


def test_duplicate_plate_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="already on line"):
        load_plates(_sheet(tmp_path, "IPR01;;a;;;;;;;;;;;1;1;\n;;b;;;;;;;;;;;;1;\n"))


def test_plate_without_run_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no IP run"):
        load_plates(_sheet(tmp_path, ";;a;;;;;;;;;;;;1;\n"))
