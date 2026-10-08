"""Tests for noxdb.fastq_qc (schema 008).

- ``TestValidate``: pure row validation, no DB.
- The rest are integration tests against the test database.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from noxdb import fastq_qc, projects, samples, subjects, transaction, visits
from noxdb.fastq_qc import _validate

from tests._helpers import wipe_all

ROW = {
    "qc_version": "0.1.0", "qc_run_at": "2026-10-08T10:15:00+02:00", "reads_r1": 3915559, "reads_r2": 3915559,
    "pair_ok": True, "sequencing_run": "@LH00402:276:22J7YMLT1", "n_lanes": 2, "lane_min_frac": 0.49512,
    "top_index": "TGTATGGTTG+TATAACCGGT", "index_purity": 0.98643, "barcode_match": "match",
    "q30_r1": 99, "q30_r2": 98.5, "avg_qual_r1": 32.42, "avg_qual_r2": 32.16, "gc_r1": 53.88, "gc_r2": 52.47,
    "dedup_r1": 12.6673, "adapter_max": 0.00291, "polyg_max": 0.11947, "n_max": 0.36697,
    "overrep_top_pct_r1": 4.3358, "depth_rel": 1.10888, "worst_flag": "OK", "flags": "",
}


class TestValidate:
    def test_converts_time_to_naive_utc(self):
        assert _validate(ROW)["qc_run_at"] == datetime(2026, 10, 8, 8, 15)

    def test_defaults(self):
        v = _validate({"qc_version": "0.1.0", "qc_run_at": datetime(2026, 1, 1), "pair_ok": 0,
                       "worst_flag": "FAIL"})
        assert v["barcode_match"] == "unknown" and v["pair_ok"] is False and v["reads_r1"] is None

    @pytest.mark.parametrize("bad", [
        {**ROW, "nope": 1},
        {k: v for k, v in ROW.items() if k != "worst_flag"},
        {**ROW, "worst_flag": "BAD"},
        {**ROW, "barcode_match": "maybe"},
    ])
    def test_rejects(self, bad):
        with pytest.raises(ValueError):
            _validate(bad)


@pytest.fixture
def sample_ids(_init_pool):
    with transaction() as cur:
        wipe_all(cur)
        pid = projects.create(cur, "QCPROJ")
        sid = subjects.create(cur, "S1", "F")
        vid = visits.create(cur, sid, "control", 30, timepoint="baseline")
        a = samples.create(cur, vid, "SMP_A", "sample", "12", "01", "libA", ipr="01", iprp="01")
        b = samples.create(cur, vid, "SMP_B", "sample", "12", "01", "libA", ipr="01", iprp="01")
        samples.link_to_project(cur, pid, a)
        samples.link_to_project(cur, pid, b)
    yield pid, a, b
    with transaction() as cur:
        wipe_all(cur)


def test_upsert_is_idempotent(sample_ids):
    _, a, _ = sample_ids
    with transaction() as cur:
        assert fastq_qc.upsert(cur, a, ROW) == "inserted"
    with transaction() as cur:
        assert fastq_qc.upsert(cur, a, ROW) == "unchanged"
    with transaction() as cur:
        assert fastq_qc.upsert(cur, a, {**ROW, "worst_flag": "WARN", "flags": "depth (WARN)"}) == "updated"
    with transaction() as cur:
        row = fastq_qc.get(cur, a)
    assert row["worst_flag"] == "WARN" and row["flags"] == "depth (WARN)"
    assert row["reads_r1"] == 3915559
    assert row["index_purity"] == Decimal("0.98643")
    assert row["qc_run_at"] == datetime(2026, 10, 8, 8, 15)


def test_get_and_delete(sample_ids):
    _, a, b = sample_ids
    with transaction() as cur:
        fastq_qc.upsert(cur, a, ROW)
    with transaction() as cur:
        assert fastq_qc.get(cur, b) is None
        assert fastq_qc.delete(cur, a) is True
        assert fastq_qc.delete(cur, a) is False


def test_fraction_check_constraint(sample_ids):
    _, a, _ = sample_ids
    with pytest.raises(Exception):
        with transaction() as cur:
            fastq_qc.upsert(cur, a, {**ROW, "index_purity": 1.5})


def test_for_project_keeps_samples_without_qc(sample_ids):
    pid, a, b = sample_ids
    with transaction() as cur:
        fastq_qc.upsert(cur, a, ROW)
    with transaction() as cur:
        df = fastq_qc.for_project(cur, pid)
    assert list(df["sample_name"]) == ["SMP_A", "SMP_B"]
    assert df.loc[df["sample_id"] == a, "worst_flag"].item() == "OK"
    assert df.loc[df["sample_id"] == b, "worst_flag"].isna().item()
    assert {"SQR", "SQRP", "sample_type", "q30_r1"} <= set(df.columns)


def test_deleting_the_sample_removes_its_qc(sample_ids):
    _, a, _ = sample_ids
    with transaction() as cur:
        fastq_qc.upsert(cur, a, ROW)
    with transaction() as cur:
        cur.execute("DELETE FROM samples WHERE sample_id = ?", (a,))
    with transaction() as cur:
        assert fastq_qc.get(cur, a) is None
