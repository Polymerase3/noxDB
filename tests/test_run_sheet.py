"""Tests for noxdb.run_sheet and scripts/apply_run_sheet.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from noxdb import projects, run_sheet, samples, subjects, transaction, visits
from noxdb.run_sheet import RunSheetRow

from tests._helpers import wipe_all

_APPLY = Path(__file__).resolve().parents[1] / "scripts" / "apply_run_sheet.py"

# The export's shape: a banner row, the column names, then one row per well.
SHEET = """\
;;;;;;;;Barcodes;;;;;;
SQR#;SQRP#;SampleName;Original sample ID;Project;Project_plate;Additional information;;i7 index;i7 index ID;i5 index;i5 index ID;;;
7;2;Sample_R25P01_01_IBD001_IBD_VIE_A_T_C2;;IBD_Vienna;;;NextSeq;taacttggtc;IDT10_i7_1;GTCGTGAATC;IDT10_i5_1;;;
07;02;R25P01_02_IBD-002_IBD_VIE_A_T_C2;;IBD_Vienna;;;NextSeq;TCGAACACGA;IDT10_i7_13;GCGAGGTTCT;IDT10_i5_13;;;
07;02;R25P01_81_Mock_1_A_T_C2;;IBD_Vienna;;;NextSeq;TTACTTCGTG;IDT10_i7_25;AAGATCGGAT;IDT10_i5_25;;;
09;01;R25P01_81_Mock_1_A_T_C2;;IBD_Vienna;;;repeat;GGACTTCGTG;IDT10_i7_26;CAGATCGGAT;IDT10_i5_26;;;
07;02;R99P09_05_NOT_IN_DB;;Other;;;NextSeq;ACGTACGTAC;IDT10_i7_40;ACGTACGTAC;IDT10_i5_40;;;
;;;;;;;;;;;;;;
"""


@pytest.fixture
def sheet(tmp_path) -> Path:
    path = tmp_path / "Overview_SQRs(All_SQRs).csv"
    path.write_text(SHEET, encoding="utf-8")
    return path


def load_apply_module():
    spec = importlib.util.spec_from_file_location("apply_run_sheet_under_test", _APPLY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Reading and matching (no database)
# --------------------------------------------------------------------------- #

def test_read_run_sheet(sheet):
    rows = run_sheet.read_run_sheet(sheet)
    assert [r.sample_name for r in rows] == [
        "R25P01_01_IBD001_IBD_VIE_A_T_C2",      # Sample_ prefix stripped
        "R25P01_02_IBD-002_IBD_VIE_A_T_C2",
        "R25P01_81_Mock_1_A_T_C2",
        "R25P01_81_Mock_1_A_T_C2",
        "R99P09_05_NOT_IN_DB",
    ]
    first = rows[0]
    assert (first.line, first.sqr, first.sqrp) == (3, "07", "02")   # padded
    assert (first.i7_index, first.i7_index_id) == ("taacttggtc", "IDT10_i7_1")
    assert (first.i5_index, first.i5_index_id) == ("GTCGTGAATC", "IDT10_i5_1")


def test_read_run_sheet_needs_the_columns(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("SQR#;SQRP#;SampleName\n07;02;X\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no header row"):
        run_sheet.read_run_sheet(path)


def test_match_by_name_then_by_well(sheet):
    rows = run_sheet.read_run_sheet(sheet)
    db = [
        {"sample_id": 1, "sample_name": "R25P01_01_IBD001_IBD_VIE_A_T_C2"},
        # the sheet writes the subject with a hyphen: found by plate and well
        {"sample_id": 2, "sample_name": "R25P01_02_IBD_002_IBD_VIE_A_T_C2"},
        {"sample_id": 3, "sample_name": "R25P01_81_Mock_1_A_T_C2"},
        {"sample_id": 4, "sample_name": "R25P01_03_ONLY_IN_DB_A_T_C2"},
    ]
    result = run_sheet.match_samples(rows, db)
    got = {m.sample["sample_id"]: (m.by, m.offered, m.row.sqr) for m in result.matches}
    assert got == {
        1: ("name", 1, "07"),
        2: ("well", 1, "07"),
        3: ("name", 2, "09"),      # sequenced twice: the later run
    }
    assert [s["sample_id"] for s in result.unmatched_samples] == [4]
    assert [r.sample_name for r in result.unused_rows] == ["R99P09_05_NOT_IN_DB"]


def test_plan_changes_reports_bad_sequences():
    match = run_sheet.Match(
        sample={"sample_id": 1, "sample_name": "S", "SQR": "", "SQRP": "", "i7_index": None,
                "i7_index_id": None, "i5_index": None, "i5_index_id": None},
        row=RunSheetRow(line=9, sample_name="S", sqr="07", sqrp="02", i7_index="AC-GT"),
        by="name", offered=1,
    )
    [change] = run_sheet.plan_changes([match])
    assert change.error.startswith("line 9: index sequence 'AC-GT'")
    with pytest.raises(ValueError, match="cannot be applied"):
        run_sheet.apply(None, [change])


# --------------------------------------------------------------------------- #
# Applying (database)
# --------------------------------------------------------------------------- #

@pytest.fixture
def db_samples(_init_pool):
    """Three samples: one without sequencing values, one complete, one to find by well."""
    with transaction() as cur:
        wipe_all(cur)
        sid = subjects.create(cur, "IBD_VIE_001", "F")
        vid = visits.create(cur, sid, "UC", 34, timepoint="baseline")
        empty = samples.create(cur, vid, "R25P01_01_IBD001_IBD_VIE_A_T_C2", "sample",
                               "", "", "A_T_C2", ipr="25", iprp="01")
        full = samples.create(cur, vid, "R25P01_81_Mock_1_A_T_C2", "mockIP",
                              "09", "01", "A_T_C2", ipr="25", iprp="01",
                              i7_index="GGACTTCGTG", i7_index_id="IDT10_i7_26",
                              i5_index="CAGATCGGAT", i5_index_id="IDT10_i5_26")
        by_well = samples.create(cur, vid, "R25P01_02_IBD_002_IBD_VIE_A_T_C2", "sample",
                                 "07", "02", "A_T_C2", ipr="25", iprp="01")
    yield {"empty": empty, "full": full, "by_well": by_well}
    with transaction() as cur:
        wipe_all(cur)


def plan(sheet, cur):
    rows = run_sheet.read_run_sheet(sheet)
    result = run_sheet.match_samples(rows, run_sheet.samples_to_match(cur))
    return run_sheet.plan_changes(result.matches)


def test_apply_fills_in_and_leaves_matching_values_alone(sheet, db_samples):
    with transaction() as cur:
        changes = plan(sheet, cur)
        # the complete sample already matches its later run: no change
        assert sorted(c.sample_name for c in changes) == [
            "R25P01_01_IBD001_IBD_VIE_A_T_C2", "R25P01_02_IBD_002_IBD_VIE_A_T_C2",
        ]
        assert all(c.conflicts == [] for c in changes)
        assert run_sheet.apply(cur, changes) == 2
        rows = {r["sample_id"]: r for r in run_sheet.samples_to_match(cur)}
    empty = rows[db_samples["empty"]]
    assert (empty["SQR"], empty["SQRP"], empty["i7_index"], empty["i5_index_id"]) == (
        "07", "02", "TAACTTGGTC", "IDT10_i5_1",
    )
    assert rows[db_samples["by_well"]]["i7_index"] == "TCGAACACGA"
    assert rows[db_samples["full"]]["SQR"] == "09"
    with transaction() as cur:
        assert plan(sheet, cur) == []          # applying again changes nothing


def test_apply_refuses_conflicts_unless_overwrite(sheet, db_samples):
    with transaction() as cur:
        samples.update(cur, db_samples["by_well"], sqr="08")
    with transaction() as cur:
        changes = plan(sheet, cur)
        [conflict] = [c for c in changes if c.conflicts]
        assert conflict.conflicts == ["SQR '08' → '07'"]
        with pytest.raises(ValueError, match="already have other sequencing values"):
            run_sheet.apply(cur, changes)
        assert samples.get(cur, db_samples["empty"])["SQR"] == ""    # nothing written
        run_sheet.apply(cur, changes, overwrite=True)
        assert samples.get(cur, db_samples["by_well"])["SQR"] == "07"


def test_samples_to_match_by_project(db_samples):
    with transaction() as cur:
        pid = projects.create(cur, "IBD_Vienna")
        samples.link_to_project(cur, pid, db_samples["empty"])
        got = run_sheet.samples_to_match(cur, project_name="IBD_Vienna")
    assert [r["sample_id"] for r in got] == [db_samples["empty"]]


def test_command_report(sheet, db_samples, tmp_path):
    """The CLI's report and --out file, built from the same plan."""
    apply_mod = load_apply_module()
    rows = run_sheet.read_run_sheet(sheet)
    with transaction() as cur:
        db = run_sheet.samples_to_match(cur)
    result = run_sheet.match_samples(rows, db)
    changes = run_sheet.plan_changes(result.matches)
    lines = apply_mod.summarize(rows, db, result, changes, project=None)
    assert "matched: 3 (2 by name, 1 by IP plate and well; 1 sequenced more than once, later run taken)" in lines
    assert "samples that would change: 2" in lines
    out = tmp_path / "changes.csv"
    apply_mod.write_changes(out, changes)
    assert out.read_text().splitlines()[0] == (
        "sample_name,column,new_value,replaces_a_different_value,error"
    )
    assert len(out.read_text().splitlines()) == 1 + 6 + 4   # empty: 6 columns, by_well: 4 barcodes
