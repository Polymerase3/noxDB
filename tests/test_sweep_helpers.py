"""Unit tests for the monitoring sweep's pure helpers (no DB required).

The sweep is a standalone script rather than part of the package, so it
is loaded by path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SWEEP = Path(__file__).resolve().parents[1] / "scripts" / "sweep" / "noxdb_sweep.py"


@pytest.fixture(scope="module")
def sweep():
    spec = importlib.util.spec_from_file_location("noxdb_sweep_under_test", _SWEEP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_padding_difference_is_one_identity(sweep):
    """The case this check exists for: one specimen registered twice."""
    padded = sweep._sample_identity("R05P01_01_0474408_KielP01_A_T_C2")
    unpadded = sweep._sample_identity("R05P01_1_0474408_KielP01_A_T_C2")
    assert padded == unpadded


def test_different_well_is_a_different_sample(sweep):
    a = sweep._sample_identity("R05P01_01_0474408_KielP01_A_T_C2")
    b = sweep._sample_identity("R05P01_02_0474414_KielP01_A_T_C2")
    assert a != b


def test_different_plate_is_a_different_sample(sweep):
    a = sweep._sample_identity("R05P01_01_0474408_KielP01_A_T_C2")
    b = sweep._sample_identity("R05P02_01_0474408_KielP01_A_T_C2")
    assert a != b


def test_leading_zeros_after_the_well_are_significant(sweep):
    """Only the well is re-padded — a subject id's zeros carry meaning."""
    a = sweep._sample_identity("R05P01_01_0474408_KielP01_A_T_C2")
    b = sweep._sample_identity("R05P01_01_474408_KielP01_A_T_C2")
    assert a != b


@pytest.mark.parametrize(
    "name",
    [
        "R02_input_01_A_T_C2",   # run-only name: no well to compare
        "no_coordinates_here",
        "",
        None,
    ],
)
def test_names_without_a_well_are_skipped(sweep, name):
    assert sweep._sample_identity(name) is None
