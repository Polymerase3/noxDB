"""Tests for validate_bundle: offline checks and top-ups checked against the DB."""

from __future__ import annotations

from pathlib import Path

import pytest

from noxdb import execute, transaction
from noxdb._import import (
    ProjectBundle,
    ProjectImportError,
    ProjectMeta,
    SampleRow,
    SubjectRow,
    ValidationResult,
    VisitRow,
    import_project_from_dir,
    validate_bundle,
)

from tests._helpers import wipe_all

pytest.importorskip("yaml")


# --------------------------------------------------------------------------- #
# Building bundles in memory, the way a form backend would
# --------------------------------------------------------------------------- #

def subj(code, sex="", origin=None, row=2):
    return SubjectRow(subject_code=code, sex=sex, origin=origin, row_num=row)


def visit(code, timepoint="baseline", group="UC", age="30", meta=None, row=2):
    return VisitRow(subject_code=code, timepoint=timepoint, group_test=group, age=age,
                    metadata=dict(meta or {}), row_num=row)


def sample(name, code, timepoint="baseline", sample_type="sample", ipr="01", iprp="01",
           sqr="05", sqrp="02", library="libA", antibody_class="IgG", meta=None, row=2,
           **barcodes):
    return SampleRow(sample_name=name, subject_code=code, timepoint=timepoint,
                     sample_type=sample_type, ipr=ipr, iprp=iprp, sqr=sqr, sqrp=sqrp,
                     library=library, antibody_class=antibody_class,
                     metadata=dict(meta or {}), row_num=row, **barcodes)


def bundle(subjects=(), visits=(), samples=(), name="BASE") -> ProjectBundle:
    return ProjectBundle(root=Path("."), project=ProjectMeta(project_name=name),
                         subjects=list(subjects), visits=list(visits), samples=list(samples))


# The rows of the BASE project, exactly as they are stored.
SUBJ_A = subj("S_A", "F", "PL")
VISIT_A = visit("S_A", meta={"bmi": 22.5})
SAMPLE_A = sample("R01P01_01_A", "S_A", meta={"well": "A01"})


def check(b: ProjectBundle) -> ValidationResult:
    with transaction() as cur:
        return validate_bundle(b, cur=cur, force=True, skip_disk_check=True)


# --------------------------------------------------------------------------- #
# Offline: no database needed
# --------------------------------------------------------------------------- #

def test_offline_collects_every_error():
    b = bundle(
        subjects=[subj("S_1", "X")],
        visits=[visit("S_1")],
        samples=[sample("N1", "S_1", sample_type="blood"), sample("N2", "S_9", row=3)],
    )
    result = validate_bundle(b)
    assert not result.ok
    joined = "\n".join(result.errors)
    assert "sex='X'" in joined
    assert "sample_type='blood'" in joined
    assert "('S_9', 'baseline') not in visits.csv" in joined


def test_offline_ok_bundle_reports_normalized_plates():
    b = bundle(subjects=[subj("S_1", "F")], visits=[visit("S_1")],
               samples=[sample("N1", "S_1", sqr=" 7")])
    result = validate_bundle(b)
    assert result.ok
    assert any("' 7' normalized to '07'" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# Against the database
# --------------------------------------------------------------------------- #

@pytest.fixture
def base_project(tmp_path, _init_pool):
    """BASE: S_A (F, PL) with a full sample; S_B without sex, age or SQR."""
    with transaction() as cur:
        wipe_all(cur)
    root = tmp_path / "base"
    root.mkdir()
    (root / "project.yaml").write_text("project_name: BASE\n")
    (root / "subjects.csv").write_text("subject_code,sex,origin\nS_A,F,PL\nS_B,,AT\n")
    (root / "visits.csv").write_text(
        "subject_code,timepoint,group_test,age,meta_bmi\n"
        "S_A,baseline,UC,30,22.5\n"
        "S_B,baseline,HC,,\n"
    )
    (root / "samples.csv").write_text(
        "sample_name,subject_code,timepoint,sample_type,ipr,iprp,sqr,sqrp,library,antibody_class,meta_well\n"
        "R01P01_01_A,S_A,baseline,sample,01,01,05,02,libA,IgG,A01\n"
        "R01P01_02_B,S_B,baseline,sample,01,01,,,libA,,A02\n"
    )
    import_project_from_dir(root, log_dir=tmp_path / "logs")
    yield root
    with transaction() as cur:
        wipe_all(cur)


def test_existing_project_needs_force(base_project):
    b = bundle(subjects=[SUBJ_A], visits=[VISIT_A], samples=[SAMPLE_A])
    with transaction() as cur:
        refused = validate_bundle(b, cur=cur, skip_disk_check=True)
        allowed = validate_bundle(b, cur=cur, force=True, skip_disk_check=True)
    assert any("already exists" in e for e in refused.errors)
    assert allowed.ok


def test_identical_top_up_is_clean(base_project):
    """Repeating stored rows verbatim, plus a new sample, raises nothing."""
    b = bundle(
        subjects=[SUBJ_A],
        visits=[VISIT_A],
        samples=[SAMPLE_A, sample("R01P01_03_A", "S_A", row=3)],
    )
    result = check(b)
    assert result.errors == []
    assert result.warnings == []


def test_subject_with_different_sex_or_origin_is_an_error(base_project):
    result = check(bundle(subjects=[subj("S_A", "M", "DE")]))
    assert result.errors == [
        "subjects.csv row 2: subject 'S_A' already exists with sex='F'; the bundle has 'M'",
        "subjects.csv row 2: subject 'S_A' already exists with origin='PL'; the bundle has 'DE'",
    ]


def test_filling_an_empty_subject_field_is_a_warning(base_project):
    result = check(bundle(subjects=[subj("S_B", "F", "AT")]))
    assert result.errors == []
    assert result.warnings == [
        "subjects.csv row 2: subject 'S_B' exists without sex; the import does not "
        "fill in sex='F' on existing rows",
    ]


def test_empty_bundle_value_asserts_nothing(base_project):
    result = check(bundle(subjects=[subj("S_A", "", None)],
                          visits=[visit("S_A", group="UC", age="")]))
    assert result.errors == []
    assert result.warnings == []


def test_visit_with_different_group_or_age_is_an_error(base_project):
    result = check(bundle(subjects=[SUBJ_A], visits=[visit("S_A", group="CD", age="31")]))
    assert result.errors == [
        "visits.csv row 2: visit ('S_A', 'baseline') already exists with group_test='UC'; "
        "the bundle has 'CD'",
        "visits.csv row 2: visit ('S_A', 'baseline') already exists with age=30; "
        "the bundle has 31",
    ]


def test_filling_an_empty_age_is_a_warning(base_project):
    result = check(bundle(subjects=[subj("S_B", "", "AT")],
                          visits=[visit("S_B", group="HC", age="44")]))
    assert result.errors == []
    assert result.warnings == [
        "visits.csv row 2: visit ('S_B', 'baseline') exists without age; the import "
        "does not fill in age=44 on existing rows",
    ]


def test_sample_under_another_visit_is_an_error(base_project):
    b = bundle(subjects=[subj("S_N", "F")], visits=[visit("S_N")],
               samples=[sample("R01P01_01_A", "S_N", meta={"well": "A01"})])
    result = check(b)
    assert result.errors == [
        "samples.csv row 2: sample 'R01P01_01_A' already exists under visit "
        "('S_A', 'baseline'); the bundle puts it under ('S_N', 'baseline')",
    ]


def test_sample_with_different_values_is_an_error(base_project):
    b = bundle(subjects=[SUBJ_A], visits=[VISIT_A],
               samples=[sample("R01P01_01_A", "S_A", ipr="1", iprp="02", sqr="06",
                               library="libB", antibody_class="IgA", meta={"well": "A01"})])
    result = check(b)
    # ipr '1' canonicalizes to the stored '01', so only the real differences remain.
    assert [e.split("already exists with ")[1] for e in result.errors] == [
        "IPRP='01'; the bundle has '02'",
        "SQR='05'; the bundle has '06'",
        "library='libA'; the bundle has 'libB'",
        "antibody_class='IgG'; the bundle has 'IgA'",
    ]


def test_setting_sqr_on_an_existing_sample_is_a_warning(base_project):
    b = bundle(subjects=[subj("S_B", "", "AT")], visits=[visit("S_B", group="HC", age="")],
               samples=[sample("R01P01_02_B", "S_B", antibody_class=None,
                               meta={"well": "A02"})])
    result = check(b)
    assert result.errors == []
    assert [w.split(": ", 1)[1] for w in result.warnings] == [
        "sample 'R01P01_02_B' exists without SQR; the import does not fill in SQR='05' "
        "on existing rows; set it with scripts/apply_run_sheet.py",
        "sample 'R01P01_02_B' exists without SQRP; the import does not fill in SQRP='02' "
        "on existing rows; set it with scripts/apply_run_sheet.py",
    ]


def test_barcodes_are_checked_like_sqr(base_project):
    """No barcodes stored: filling them in warns. A stored one that differs is an error."""
    fill = check(bundle(subjects=[SUBJ_A], visits=[VISIT_A],
                        samples=[sample("R01P01_01_A", "S_A", meta={"well": "A01"},
                                        i7_index="acgtacgtac", i7_index_id="IDT10_i7_1")]))
    assert fill.errors == []
    assert [w.split(" exists without ")[1].split(";")[0] for w in fill.warnings] == [
        "i7_index", "i7_index_id",
    ]
    assert "i7_index='ACGTACGTAC'" in fill.warnings[0]

    with transaction() as cur:
        cur.execute("UPDATE samples SET i7_index = ? WHERE sample_name = ?",
                    ("TTTTTTTTTT", "R01P01_01_A"))
    differ = check(bundle(subjects=[SUBJ_A], visits=[VISIT_A],
                          samples=[sample("R01P01_01_A", "S_A", meta={"well": "A01"},
                                          i7_index="ACGTACGTAC")]))
    assert differ.errors == [
        "samples.csv row 2: sample 'R01P01_01_A' already exists with "
        "i7_index='TTTTTTTTTT'; the bundle has 'ACGTACGTAC'",
    ]


def test_bad_barcodes_are_schema_errors():
    b = bundle(subjects=[subj("S_1", "F")], visits=[visit("S_1")],
               samples=[sample("N1", "S_1", i7_index="ACGT-ACGT", i5_index="A" * 33,
                               i5_index_id="x" * 51)])
    assert validate_bundle(b).errors == [
        "samples.csv row 2.i7_index: index sequence 'ACGT-ACGT' may only contain "
        "A, C, G, T and N",
        f"samples.csv row 2.i5_index: {'A' * 33!r} is longer than 32 characters",
        f"samples.csv row 2.i5_index_id: {'x' * 51!r} is longer than 50 characters",
    ]


def test_metadata_overwrite_is_a_warning(base_project):
    b = bundle(subjects=[SUBJ_A], visits=[visit("S_A", meta={"bmi": 24.0, "smoker": True})],
               samples=[sample("R01P01_01_A", "S_A", meta={"well": "B01"})])
    result = check(b)
    assert result.errors == []
    assert result.warnings == [
        "visits.csv row 2: visit ('S_A', 'baseline') metadata bmi=Decimal('22.500000') "
        "will be overwritten with 24.0",
        "samples.csv row 2: sample 'R01P01_01_A' metadata well='A01' will be "
        "overwritten with 'B01'",
    ]


def test_unchanged_numeric_metadata_is_not_reported(base_project):
    """22.5 comes back as Decimal('22.500000'); that is the same value."""
    result = check(bundle(subjects=[SUBJ_A], visits=[visit("S_A", meta={"bmi": 22.5})]))
    assert result.warnings == []


def test_lookups_run_in_chunks(base_project):
    """More values than one IN (...) query takes; none of them exist."""
    many = [subj(f"NEW_{i}", "F", row=i + 2) for i in range(1203)]
    result = check(bundle(subjects=many))
    assert result.errors == []


def test_import_refuses_a_conflicting_top_up_and_writes_nothing(base_project, tmp_path):
    top_up = tmp_path / "top_up"
    top_up.mkdir()
    (top_up / "project.yaml").write_text("project_name: BASE\n")
    (top_up / "subjects.csv").write_text("subject_code,sex,origin\nS_A,F,PL\nS_C,M,AT\n")
    (top_up / "visits.csv").write_text(
        "subject_code,timepoint,group_test,age\nS_A,baseline,UC,35\nS_C,baseline,UC,50\n"
    )
    (top_up / "samples.csv").write_text(
        "sample_name,subject_code,timepoint,sample_type,ipr,iprp,sqr,sqrp,library\n"
        "R01P01_04_C,S_C,baseline,sample,01,01,05,02,libA\n"
    )
    with pytest.raises(ProjectImportError) as exc:
        import_project_from_dir(top_up, force=True, log_dir=tmp_path / "logs")
    assert exc.value.errors == [
        "visits.csv row 2: visit ('S_A', 'baseline') already exists with age=30; "
        "the bundle has 35",
    ]
    assert execute("SELECT COUNT(*) AS n FROM subjects WHERE subject_code = ?", ("S_C",))[0]["n"] == 0
    assert execute(
        "SELECT age FROM visits v JOIN subjects s ON s.subject_id = v.subject_id "
        "WHERE s.subject_code = ?", ("S_A",),
    )[0]["age"] == 30


def test_bulk_import_reports_the_same_conflicts(base_project, tmp_path):
    """scripts/bulk_import.py runs these checks too (through validate_bundle)."""
    from tests.test_bulk_import import load_bulk_module, write_master

    bulk = load_bulk_module()

    def run(folder: Path, *, dry_run: bool):
        [b] = bulk.load_bundles(folder)
        return bulk._import_bundle(
            b, dry_run=dry_run, force=True, compute_md5=False,
            skip_disk_check=True, log_dir=tmp_path / "logs",
        )

    write_master(tmp_path / "first")
    assert run(tmp_path / "first", dry_run=False).errors == []

    # Same project again, with the stored visit's age changed.
    write_master(tmp_path / "again")
    (tmp_path / "again" / "visits.csv").write_text(
        "project_name,subject_code,timepoint,group_test,age\nBULK_A,S_1,baseline,UC,41\n"
    )
    assert run(tmp_path / "again", dry_run=True).errors == [
        "visits.csv row 2: visit ('S_1', 'baseline') already exists with age=40; "
        "the bundle has 41",
    ]
