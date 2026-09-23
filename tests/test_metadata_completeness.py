"""Unit tests for per-project metadata completeness (no DB required)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[1] / "scripts" / "sweep" / "metadata_completeness.py"


@pytest.fixture(scope="module")
def mc():
    spec = importlib.util.spec_from_file_location("metadata_completeness_under_test", _MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# (project, sample_id, visit_id, subject_id, sex, age, group_test, timepoint)
ROWS = [
    ("Long", 1, 10, 100, "F", 40, "UC", "BL"),
    ("Long", 2, 11, 100, "F", 41, "UC", "FU"),       # same subject, second timepoint
    ("Long", 3, 12, 101, None, None, "unknown", "BL"),
    ("Flat", 4, 20, 200, "M", 30, "HC", "baseline"),
    ("Flat", 5, 20, 200, "M", 30, "HC", "baseline"),  # re-run of the same visit
]


def test_percentages_are_per_subject_and_per_visit(mc):
    r = mc.compute_completeness(ROWS, {10: {"CRP"}, 11: {"CRP"}}, with_counts={1, 2, 4})
    long_ = next(p for p in r["projects"] if p["project"] == "Long")
    assert (long_["subjects"], long_["visits"], long_["samples"]) == (2, 3, 3)
    assert long_["has_sex"] == 50.0          # 1 of 2 subjects
    assert long_["has_age"] == pytest.approx(66.7)
    assert long_["has_group"] == pytest.approx(66.7)   # 'unknown' does not count
    assert long_["meta_fields"] == {"CRP": pytest.approx(66.7)}
    assert long_["has_counts"] == pytest.approx(66.7)


def test_longitudinal_needs_two_timepoints_for_one_subject(mc):
    r = mc.compute_completeness(ROWS, {}, set())
    by = {p["project"]: p for p in r["projects"]}
    assert by["Long"]["is_longitudinal"] and by["Long"]["multi_timepoint_subjects"] == 50.0
    assert not by["Flat"]["is_longitudinal"]     # two samples, one visit
    assert r["longitudinal"] == 1


def test_summary_counts_complete_projects(mc):
    r = mc.compute_completeness(ROWS, {}, set())
    assert (r["complete_all"], r["n_projects"]) == (1, 2)   # only Flat is fully annotated
    assert mc.summary_line(r).startswith("1 of 2 projects")
    assert [p["project"] for p in mc.worst_first(r["projects"])] == ["Long", "Flat"]
