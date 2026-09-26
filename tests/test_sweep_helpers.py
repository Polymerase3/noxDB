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
    padded = sweep._sample_identity("R05P01_01_0123456_IBDP01_A_T_C2", "05", "01")
    unpadded = sweep._sample_identity("R05P01_1_0123456_IBDP01_A_T_C2", "05", "01")
    assert padded == unpadded


def test_different_well_is_a_different_sample(sweep):
    a = sweep._sample_identity("R05P01_01_0123456_IBDP01_A_T_C2", "05", "01")
    b = sweep._sample_identity("R05P01_02_0123457_IBDP01_A_T_C2", "05", "01")
    assert a != b


def test_different_plate_is_a_different_sample(sweep):
    a = sweep._sample_identity("R05P01_01_0123456_IBDP01_A_T_C2", "05", "01")
    b = sweep._sample_identity("R05P02_01_0123456_IBDP01_A_T_C2", "05", "02")
    assert a != b


def test_plate_is_the_stored_one_not_the_name_prefix(sweep):
    """Two plates that files labelled alike are still two plates."""
    a = sweep._sample_identity("R08P01_01_x_A_T_C2", "08", "01")
    b = sweep._sample_identity("R08P01_01_x_A_T_C2", "08", "04")
    assert a != b


def test_leading_zeros_after_the_well_are_significant(sweep):
    """Only the well is re-padded — a subject id's zeros carry meaning."""
    a = sweep._sample_identity("R05P01_01_0123456_IBDP01_A_T_C2", "05", "01")
    b = sweep._sample_identity("R05P01_01_123456_IBDP01_A_T_C2", "05", "01")
    assert a != b


@pytest.mark.parametrize(
    "name, ipr",
    [
        ("R02_input_01_A_T_C2", "02"),   # run-only name: no well to compare
        ("no_coordinates_here", "07"),
        ("R05P01_01_x_A_T_C2", ""),      # no stored plate
        ("", "05"),
        (None, "05"),
    ],
)
def test_names_without_a_well_are_skipped(sweep, name, ipr):
    assert sweep._sample_identity(name, ipr, "01") is None


def test_dir_sizes_counts_the_bytes_under_each_folder(sweep, tmp_path):
    (tmp_path / "counts").mkdir()
    (tmp_path / "counts" / "a.tsv").write_bytes(b"x" * 5000)
    (tmp_path / "zigp").mkdir()
    sizes = sweep.dir_sizes([tmp_path / "counts", tmp_path / "zigp"])
    assert set(sizes) == {str(tmp_path / "counts"), str(tmp_path / "zigp")}
    assert sizes[str(tmp_path / "counts")] >= 5000 > sizes[str(tmp_path / "zigp")]


def test_fmt_bytes_uses_decimal_units(sweep):
    assert sweep.fmt_bytes(512) == "512 B"
    assert sweep.fmt_bytes(25_067_589_684) == "25.07 GB"
    assert sweep.fmt_bytes(3_156_840_345_629) == "3.16 TB"
    assert sweep.fmt_bytes(-2_500_000) == "-2.50 MB"
