"""Tests for scripts/bulk_import.py (the master-CSV importer).

These need no database. The bulk path's database checks are tested in
test_validate_bundle.py: a database test in this file would sort before
test_connection.py, whose tests reset the pool the session shares.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_BULK = Path(__file__).resolve().parents[1] / "scripts" / "bulk_import.py"

SUBJECTS_CSV = "project_name,subject_code,sex,origin\nBULK_A,S_1,F,AT\n"


def load_bulk_module():
    spec = importlib.util.spec_from_file_location("bulk_import_under_test", _BULK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_master(root: Path, *, subjects_csv: str = SUBJECTS_CSV) -> None:
    """A one-project master folder: subject S_1, visit at age 40, one sample."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects.csv").write_text("project_name,description\nBULK_A,first\n")
    (root / "subjects.csv").write_text(subjects_csv)
    (root / "visits.csv").write_text(
        "project_name,subject_code,timepoint,group_test,age,meta_bmi\n"
        "BULK_A,S_1,baseline,UC,40,22.5\n"
    )
    (root / "samples.csv").write_text(
        "project_name,sample_name,subject_code,timepoint,sample_type,ipr,iprp,sqr,sqrp,library\n"
        "BULK_A,R01P01_01_S1_libA,S_1,baseline,sample,01,01,02,01,libA\n"
    )


@pytest.fixture(scope="module")
def bulk():
    return load_bulk_module()


def test_load_bundles_reads_one_project(tmp_path, bulk):
    write_master(tmp_path)
    [bundle] = bulk.load_bundles(tmp_path)
    assert bundle.project.project_name == "BULK_A"
    assert [s.subject_code for s in bundle.subjects] == ["S_1"]
    assert bundle.visits[0].metadata == {"bmi": 22.5}


def test_subject_meta_columns_are_refused(tmp_path, bulk):
    """Same rule as the folder importer: no subject-level metadata."""
    write_master(
        tmp_path,
        subjects_csv="project_name,subject_code,sex,origin,meta_diagnosis\nBULK_A,S_1,F,AT,UC\n",
    )
    with pytest.raises(ValueError) as exc:
        bulk.load_bundles(tmp_path)
    assert "meta_diagnosis" in str(exc.value)
    assert "visits.csv" in str(exc.value)
