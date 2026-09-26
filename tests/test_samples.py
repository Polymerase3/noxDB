"""Integration tests for noxdb.samples (require a live MariaDB)."""

from __future__ import annotations

import mariadb
import pytest

from noxdb import projects, samples, subjects, transaction, visits

from tests._helpers import wipe_all


@pytest.fixture
def two_visits(_init_pool):
    """Two visits under one subject. Cleanup wipes everything via cascade."""
    with transaction() as cur:
        wipe_all(cur)
        projects.create(cur, "SPROJ")
        sid = subjects.create(cur, "S1", "F")
        v1 = visits.create(cur, sid, "control", 30, timepoint="baseline")
        v2 = visits.create(cur, sid, "control", 31, timepoint="m3")
    yield v1, v2
    with transaction() as cur:
        wipe_all(cur)


# --------------------------------------------------------------------------- #
# create / get / get_by_name
# --------------------------------------------------------------------------- #

def test_create_returns_new_id_and_persists(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "SAMP_A", "sample", "SQR1", "SQRP1", "libA",
            antibody_class="IgG",
            ipr="01", iprp="01",
        )
        assert isinstance(sid, int) and sid > 0
        row = samples.get(cur, sid)
    assert row["visit_id"] == v1
    assert row["sample_name"] == "SAMP_A"
    assert row["sample_type"] == "sample"
    assert row["SQR"] == "SQR1"
    assert row["SQRP"] == "SQRP1"
    assert row["library"] == "libA"
    assert row["antibody_class"] == "IgG"


def test_create_stores_the_ip_coords_it_is_given(two_visits):
    """IPR/IPRP come from the caller, never from the name.

    The name carries a different RxxPxx and the sequencing coordinates
    differ again, so a test that read either would fail here.
    """
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "R02P01_01_DEMOp1_1002_A_T_C2", "sample",
            "07", "02", "libA", ipr="04", iprp="03",
        )
        row = samples.get(cur, sid)
    assert (row["IPR"], row["IPRP"]) == ("04", "03")
    assert (row["SQR"], row["SQRP"]) == ("07", "02")


def test_create_canonicalizes_ip_coords(two_visits):
    """Unpadded input stores the padded form, so IPR+IPRP matching
    compares byte-for-byte against a padded row."""
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "CANON_IP", "sample", "11", "03", "libA", ipr=" 5", iprp="1",
        )
        row = samples.get(cur, sid)
    assert (row["IPR"], row["IPRP"]) == ("05", "01")


def test_create_ip_plate_empty_for_a_run_only_sample(two_visits):
    """An input carries a run and no plate, matching how SQRP models
    'no plate' as '' rather than NULL."""
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "R02_input_01_A_T_C2", "input", "02", "", "libA",
            ipr="02", iprp="NA",
        )
        row = samples.get(cur, sid)
    assert (row["IPR"], row["IPRP"]) == ("02", "")


def test_create_requires_ip_coords(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        with pytest.raises(TypeError, match="ipr"):
            samples.create(cur, v1, "R05P01_1_x", "sample", "07", "02", "libA")


def test_update_can_correct_ip_coords(two_visits):
    """A stored row must be fixable when the sheet is corrected."""
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "R05P01_1_x_A_T_C2", "sample", "07", "02", "libA", ipr="01", iprp="01")
        samples.update(cur, sid, ipr=" 9 ", iprp="N/A")
        row = samples.get(cur, sid)
    assert (row["IPR"], row["IPRP"]) == ("09", "")


def test_update_leaves_ip_coords_alone_when_not_given(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "R42P02_09_x_A_T_C2", "sample", "07", "02", "libA", ipr="42", iprp="02")
        samples.update(cur, sid, sqr="12", sqrp="04")
        row = samples.get(cur, sid)
    assert (row["IPR"], row["IPRP"]) == ("42", "02")
    assert (row["SQR"], row["SQRP"]) == ("12", "04")


def test_create_canonicalizes_sqr_sqrp(two_visits):
    """Whitespace is stripped; NA/empty SQRP collapses to '' so
    SQR+SQRP plate matching never drifts. Padding is preserved."""
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "CANON1", "sample", "  01 ", "NA", "libA",
            ipr="01", iprp="01",
        )
        row = samples.get(cur, sid)
    assert row["SQR"] == "01"
    assert row["SQRP"] == ""


def test_create_canonicalizes_na_sqr(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "CANON2", "sample", "n/a", "", "libA",
            ipr="01", iprp="01",
        )
        row = samples.get(cur, sid)
    assert row["SQR"] == ""
    assert row["SQRP"] == ""


def test_update_canonicalizes_sqr(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "CANON3", "sample", "05", "06", "libA", ipr="01", iprp="01")
        samples.update(cur, sid, sqr=" 07 ", sqrp="N/A")
        row = samples.get(cur, sid)
    assert row["SQR"] == "07"
    assert row["SQRP"] == ""


def test_create_allows_null_antibody_class(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "SAMP_NO_AB", "input", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        row = samples.get(cur, sid)
    assert row["antibody_class"] is None


def test_create_duplicate_name_raises(two_visits):
    """sample_name is GLOBALLY unique — duplicates across visits also fail."""
    v1, v2 = two_visits
    with transaction() as cur:
        samples.create(cur, v1, "DUP", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
    with pytest.raises(mariadb.IntegrityError):
        with transaction() as cur:
            samples.create(cur, v2, "DUP", "sample", "SQR2", "SQRP2", "libB", ipr="01", iprp="01")


def test_create_unknown_visit_id_raises(two_visits):
    with pytest.raises(mariadb.IntegrityError):
        with transaction() as cur:
            samples.create(
                cur, 9_999_999, "ORPHAN", "sample", "SQR1", "SQRP1", "libA",
                ipr="01", iprp="01",
            )


def test_create_invalid_sample_type_raises(two_visits):
    v1, _ = two_visits
    with pytest.raises(mariadb.Error):
        with transaction() as cur:
            samples.create(cur, v1, "BADTYPE", "wrong", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")


def test_get_missing_returns_none(two_visits):
    with transaction() as cur:
        assert samples.get(cur, 9_999_999) is None


def test_get_by_name_missing_returns_none(two_visits):
    with transaction() as cur:
        assert samples.get_by_name(cur, "nope") is None


def test_get_by_name_returns_row(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "BYNAME", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        row = samples.get_by_name(cur, "BYNAME")
    assert row["sample_id"] == sid


# --------------------------------------------------------------------------- #
# get_or_create
# --------------------------------------------------------------------------- #

def test_get_or_create_inserts_when_missing(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid, created = samples.get_or_create(
            cur, v1, "GOC", "sample", "SQR1", "SQRP1", "libA",
            antibody_class="IgG",
            ipr="01", iprp="01",
        )
    assert created is True
    with transaction() as cur:
        assert samples.get(cur, sid)["antibody_class"] == "IgG"


def test_get_or_create_returns_existing_without_modifying(two_visits):
    v1, v2 = two_visits
    with transaction() as cur:
        first_id = samples.create(
            cur, v1, "GOC2", "sample", "SQR1", "SQRP1", "libA",
            antibody_class="IgG",
            ipr="01", iprp="01",
        )
    with transaction() as cur:
        sid, created = samples.get_or_create(
            cur, v2, "GOC2", "input", "SQR9", "SQRP9", "libZ",
            antibody_class="ignored",
            ipr="01", iprp="01",
        )
    assert sid == first_id
    assert created is False
    with transaction() as cur:
        row = samples.get(cur, sid)
    assert row["visit_id"] == v1  # NOT moved to v2
    assert row["library"] == "libA"
    assert row["antibody_class"] == "IgG"


# --------------------------------------------------------------------------- #
# list_for_visit / count_for_visit
# --------------------------------------------------------------------------- #

def test_list_for_visit_orders_by_sample_id(two_visits):
    v1, v2 = two_visits
    with transaction() as cur:
        a = samples.create(cur, v1, "A", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        b = samples.create(cur, v1, "B", "input",  "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        samples.create(cur, v2, "C", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
    with transaction() as cur:
        rows = samples.list_for_visit(cur, v1)
    assert [r["sample_id"] for r in rows] == [a, b]


def test_list_for_visit_rejects_unknown_order_by(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        with pytest.raises(ValueError):
            samples.list_for_visit(cur, v1, order_by="; DROP TABLE samples")


def test_count_for_visit_isolated_per_visit(two_visits):
    v1, v2 = two_visits
    with transaction() as cur:
        samples.create(cur, v1, "A", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        samples.create(cur, v1, "B", "input",  "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        samples.create(cur, v2, "C", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
    with transaction() as cur:
        assert samples.count_for_visit(cur, v1) == 2
        assert samples.count_for_visit(cur, v2) == 1


# --------------------------------------------------------------------------- #
# update
# --------------------------------------------------------------------------- #

def test_update_partial_only_changes_provided_fields(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "U1", "sample", "SQR1", "SQRP1", "libA",
            antibody_class="IgG",
            ipr="01", iprp="01",
        )
    with transaction() as cur:
        changed = samples.update(cur, sid, library="libB")
    assert changed is True
    with transaction() as cur:
        row = samples.get(cur, sid)
    assert row["library"] == "libB"
    assert row["SQR"] == "SQR1"
    assert row["antibody_class"] == "IgG"


def test_update_with_all_none_is_noop(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "U2", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
    with transaction() as cur:
        assert samples.update(cur, sid) is False


def test_update_unknown_id_returns_false(two_visits):
    with transaction() as cur:
        assert samples.update(cur, 9_999_999, library="x") is False


def test_update_does_not_expose_visit_id(two_visits):
    v1, v2 = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "MOVE", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        with pytest.raises(TypeError):
            samples.update(cur, sid, visit_id=v2)


# --------------------------------------------------------------------------- #
# delete / exists
# --------------------------------------------------------------------------- #

def test_delete_returns_true_when_row_removed(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "D1", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
    with transaction() as cur:
        assert samples.delete(cur, sid) is True
        assert samples.get(cur, sid) is None


def test_delete_unknown_id_returns_false(two_visits):
    with transaction() as cur:
        assert samples.delete(cur, 9_999_999) is False


def test_delete_cascades_to_sample_metadata(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "DCASC", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        cur.execute(
            "INSERT INTO sample_metadata "
            "(sample_id, key_name, value_text, value_type) VALUES (?, ?, ?, ?)",
            (sid, "well", "A01", "text"),
        )
    with transaction() as cur:
        samples.delete(cur, sid)
        cur.execute(
            "SELECT COUNT(*) FROM sample_metadata WHERE sample_id = ?", (sid,)
        )
        assert cur.fetchone()[0] == 0


def test_delete_blocked_by_sample_files(two_visits):
    """sample_files uses ON DELETE RESTRICT — sample deletes must fail
    while a file row references the sample."""
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "DREST", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
        cur.execute(
            "INSERT INTO sample_files "
            "(sample_id, file_type, file_path) VALUES (?, ?, ?)",
            (sid, "fastq_r1", "/data/x.fastq.gz"),
        )
    with pytest.raises(mariadb.IntegrityError):
        with transaction() as cur:
            samples.delete(cur, sid)


def test_exists_by_id(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(cur, v1, "E1", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
    with transaction() as cur:
        assert samples.exists(cur, sid) is True
        assert samples.exists(cur, 9_999_999) is False


def test_exists_by_name(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        samples.create(cur, v1, "E2", "sample", "SQR1", "SQRP1", "libA", ipr="01", iprp="01")
    with transaction() as cur:
        assert samples.exists(cur, name="E2") is True
        assert samples.exists(cur, name="missing") is False


def test_exists_requires_exactly_one_arg(two_visits):
    with transaction() as cur:
        with pytest.raises(ValueError):
            samples.exists(cur)
        with pytest.raises(ValueError):
            samples.exists(cur, 1, name="x")


# --------------------------------------------------------------------------- #
# barcodes (schema 007)
# --------------------------------------------------------------------------- #

def _plain_sample(cur, visit_id, name="SEQ_A", sqr="", sqrp=""):
    return samples.create(cur, visit_id, name, "sample", sqr, sqrp, "libA", ipr="01", iprp="01")


def test_create_stores_barcodes_upper_case(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = samples.create(
            cur, v1, "BC_A", "sample", "07", "02", "libA", ipr="01", iprp="01",
            i7_index=" taacttggtc ", i7_index_id="IDT10_i7_1",
            i5_index="GTCGTGAATC", i5_index_id=" IDT10_i5_1",
        )
        row = samples.get(cur, sid)
    assert (row["i7_index"], row["i7_index_id"]) == ("TAACTTGGTC", "IDT10_i7_1")
    assert (row["i5_index"], row["i5_index_id"]) == ("GTCGTGAATC", "IDT10_i5_1")


def test_barcodes_default_to_null(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        row = samples.get(cur, _plain_sample(cur, v1))
    assert [row[c] for c in ("i7_index", "i7_index_id", "i5_index", "i5_index_id")] == [None] * 4


def test_create_rejects_a_bad_index_sequence(two_visits):
    v1, _ = two_visits
    with pytest.raises(ValueError, match="A, C, G, T and N"):
        with transaction() as cur:
            samples.create(cur, v1, "BC_BAD", "sample", "07", "02", "libA",
                           ipr="01", iprp="01", i7_index="ACGT-ACGT")


def test_db_rejects_a_lower_case_index(two_visits):
    """The CHECK is case sensitive even under the case-insensitive collation."""
    v1, _ = two_visits
    with transaction() as cur:
        sid = _plain_sample(cur, v1)
    with pytest.raises(mariadb.Error):
        with transaction() as cur:
            cur.execute("UPDATE samples SET i7_index = ? WHERE sample_id = ?", ("acgt", sid))


def test_update_sets_barcodes(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = _plain_sample(cur, v1)
        assert samples.update(cur, sid, i5_index="gtcgtgaatc", i5_index_id="IDT10_i5_1")
        row = samples.get(cur, sid)
    assert (row["i5_index"], row["i5_index_id"]) == ("GTCGTGAATC", "IDT10_i5_1")
    assert row["i7_index"] is None


# --------------------------------------------------------------------------- #
# set_sequencing
# --------------------------------------------------------------------------- #

def test_sequencing_changes_separates_fills_from_conflicts():
    stored = {"SQR": "05", "SQRP": "", "i7_index": None, "i7_index_id": None,
              "i5_index": "AAAA", "i5_index_id": None}
    changes, conflicts = samples.sequencing_changes(
        stored, sqr="5", sqrp="2", i7_index="acgt", i5_index="CCCC",
    )
    # sqr '5' is the stored '05'; sqrp and i7 fill empty columns; i5 replaces.
    assert changes == {"SQRP": "02", "i7_index": "ACGT", "i5_index": "CCCC"}
    assert conflicts == ["i5_index 'AAAA' → 'CCCC'"]


def test_set_sequencing_fills_in_empty_values(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = _plain_sample(cur, v1)
        assert samples.set_sequencing(cur, sid, sqr="7", sqrp="2", i7_index="taacttggtc",
                                      i7_index_id="IDT10_i7_1") is True
        row = samples.get(cur, sid)
    assert (row["SQR"], row["SQRP"], row["i7_index"], row["i7_index_id"]) == (
        "07", "02", "TAACTTGGTC", "IDT10_i7_1",
    )


def test_set_sequencing_same_values_change_nothing(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = _plain_sample(cur, v1, sqr="07", sqrp="02")
        assert samples.set_sequencing(cur, sid, sqr="07", sqrp="2") is False


def test_set_sequencing_leaves_values_not_given_alone(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = _plain_sample(cur, v1, sqr="07", sqrp="02")
        samples.set_sequencing(cur, sid, i5_index="GTCGTGAATC")
        row = samples.get(cur, sid)
    assert (row["SQR"], row["SQRP"], row["i5_index"]) == ("07", "02", "GTCGTGAATC")


def test_set_sequencing_refuses_to_replace_a_different_value(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = _plain_sample(cur, v1, sqr="07", sqrp="02")
    with pytest.raises(ValueError, match=r"SQR '07' → '08'.*overwrite=True"):
        with transaction() as cur:
            samples.set_sequencing(cur, sid, sqr="08", sqrp="02")
    with transaction() as cur:
        assert samples.get(cur, sid)["SQR"] == "07"


def test_set_sequencing_overwrite_replaces(two_visits):
    v1, _ = two_visits
    with transaction() as cur:
        sid = _plain_sample(cur, v1, sqr="07", sqrp="02")
        assert samples.set_sequencing(cur, sid, sqr="08", overwrite=True) is True
        assert samples.get(cur, sid)["SQR"] == "08"


def test_set_sequencing_unknown_sample_raises(two_visits):
    with pytest.raises(ValueError, match="no sample with sample_id"):
        with transaction() as cur:
            samples.set_sequencing(cur, 9_999_999, sqr="07")
