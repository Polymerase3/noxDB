"""Unit tests for the sequencing-coverage module (no DB required).

``sqr_coverage`` is a standalone script next to the sweep, so it is loaded
by path, like ``test_sweep_helpers.py`` does for the sweep itself.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[1] / "scripts" / "sweep" / "sqr_coverage.py"


@pytest.fixture(scope="module")
def cov():
    spec = importlib.util.spec_from_file_location("sqr_coverage_under_test", _MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(name, label="P1", kind="sample", sqr="07", seq_name=None, note="", **flags):
    return {"sqr": sqr, "sqrp": "01", "sheet_name": name, "seq_name": name if seq_name is None else seq_name,
            "kind": kind, "label": label, "status_note": note,
            **{f: flags.get(f, "0") for f in ("has_counts", "has_fastq", "has_bam", "has_parquet")}}


def test_sheet_spelling_matches_the_sequenced_name(cov):
    """Run sheets differ from sequenced names in separators and well padding."""
    master = [_row("R16P01_01_9636-20240429-YYP1-A01_CRCradiotherapyP1_A_T_C2", seq_name="")]
    db = {"R16P01_1_9636_20240429_YYP1_A01_CRCradiotherapyP1_A_T_C2": ["CRC_radiotherapy"]}
    r = cov.compute_coverage(master, db)
    assert r["samples_in_db"] == 1
    assert r["by_label"][0]["noxdb_projects"] == ["CRC_radiotherapy"]
    assert r["db_samples_not_in_master"] == 0


def test_project_tag_added_by_noxdb_still_matches(cov):
    """PIC/SAR: files and run sheet say ..._SAR19_A_T_C2, noxDB says ..._SAR19_PIC_SAR_MUW_A_T_C2."""
    r = cov.compute_coverage([_row("R42P02_01_SAR19_A_T_C2")], {"R42P02_01_SAR19_PIC_SAR_MUW_A_T_C2": ["SAR_MUW"]})
    assert r["samples_in_db"] == 1
    assert r["db_samples_not_in_master"] == 0


def test_same_well_different_sample_does_not_match(cov):
    """R08: PREDICTS and PCa_Innsbruck plates share IP names but not patient IDs."""
    r = cov.compute_coverage([_row("R08P01_01_S839704101_PREDICTSP05_A_T_C2")],
                             {"R08P01_01_294299_PCaInnsbruckP01_A_T_C2": ["PCa_Innsbruck"]})
    assert r["samples_in_db"] == 0
    assert r["db_samples_not_in_master"] == 1


def test_project_status_complete_partial_absent(cov):
    master = [
        _row("R01P01_01_a_A_T_C2", label="done"),
        _row("R01P01_02_b_A_T_C2", label="half"), _row("R01P01_03_c_A_T_C2", label="half"),
        _row("R01P01_04_d_A_T_C2", label="none", note="held: no metadata"),
    ]
    db = {"R01P01_01_a_A_T_C2": ["X"], "R01P01_02_b_A_T_C2": ["Y"]}
    r = cov.compute_coverage(master, db)
    status = {x["label"]: x["status"] for x in r["by_label"]}
    assert status == {"done": "complete", "half": "partial", "none": "absent"}
    assert (r["projects_complete"], r["projects_partial"], r["projects_absent"]) == (1, 1, 1)
    assert r["by_label"][2]["why_missing"] == {"held: no metadata": 1}


def test_controls_are_counted_apart_from_samples(cov):
    master = [_row("R01P01_01_a_A_T_C2"), _row("R01P01_81_Mock_1_A_T_C2", kind="mockIP", label="")]
    db = {"R01P01_81_Mock_1_A_T_C2": []}
    r = cov.compute_coverage(master, db)
    assert (r["samples"], r["samples_in_db"]) == (1, 0)
    assert (r["controls"], r["controls_in_db"]) == (1, 1)
    assert r["by_sqr"]["07"] == {"samples": 1, "samples_in_db": 0, "controls": 1, "controls_in_db": 1}


def test_db_samples_missing_from_the_sheet_are_reported(cov):
    r = cov.compute_coverage([_row("R01P01_01_a_A_T_C2")], {"R01P01_01_a_A_T_C2": ["X"], "R99P01_01_z_A_T_C2": ["X"]})
    assert r["db_samples_not_in_master"] == 1
    assert r["db_samples_not_in_master_sample"] == ["R99P01_01_z_A_T_C2"]


def test_file_flags_are_summed_over_samples_only(cov):
    master = [_row("R01P01_01_a_A_T_C2", has_counts="1", has_fastq="1"),
              _row("R01P01_02_b_A_T_C2", has_bam="1"),
              _row("R01P01_81_Mock_1_A_T_C2", kind="mockIP", has_counts="1")]
    r = cov.compute_coverage(master, {})
    assert r["file_flags"] == {"has_counts": 1, "has_fastq": 1, "has_bam": 1, "has_parquet": 0}


def test_summary_line(cov):
    r = cov.compute_coverage([_row("R01P01_01_a_A_T_C2")], {"R01P01_01_a_A_T_C2": ["X"]})
    assert cov.summary_line(r).startswith("1 of 1 sequenced samples in noxDB (100.0%)")
