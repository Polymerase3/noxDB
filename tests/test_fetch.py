"""Integration tests for noxdb.fetch (require a live MariaDB + pandas).

The download tests exercise the local-copy path of the transport
abstraction — no SSH is involved. SFTP-specific behavior is covered by
a dedicated test that monkeypatches :mod:`paramiko`.
"""

from __future__ import annotations

import pytest

from noxdb import (
    fetch,
    metadata,
    projects,
    samples,
    subjects,
    transaction,
    visits,
)

from tests._helpers import wipe_all


pd = pytest.importorskip("pandas")


# --------------------------------------------------------------------------- #
# Fixture: small project with real on-disk files
# --------------------------------------------------------------------------- #

@pytest.fixture
def project_with_local_files(_init_pool, tmp_path):
    """Seed a project with 2 samples × 2 files each, written to tmp_path.

    File rows are inserted via raw SQL (paths live under tmp_path, which is
    NOT under any LABDB tier root, so files.register would reject them).
    """
    archive = tmp_path / "src" / "archive"
    work = tmp_path / "src" / "work"
    archive.mkdir(parents=True)
    work.mkdir(parents=True)

    f_a1_r1 = archive / "A1_R1.fastq.gz"
    f_a1_bam = archive / "A1.bam"
    f_b1_norm = work / "B1.beer.tsv"
    for f, payload in [
        (f_a1_r1, b"fastq-payload-a1"),
        (f_a1_bam, b"bam-payload-a1"),
        (f_b1_norm, b"norm-payload-b1"),
    ]:
        f.write_bytes(payload)

    with transaction() as cur:
        wipe_all(cur)
        pid = projects.create(cur, "FETCH_P", pi_name="Dr. F")
        sa = subjects.create(cur, "S_A", "F", origin="PL")
        va = visits.create(cur, sa, "ctrl", 30, timepoint="baseline")
        a1 = samples.create(cur, va, "S_A1", "sample", "Q", "Q", "libA",
                            antibody_class="IgG", ipr="01", iprp="01")
        b1 = samples.create(cur, va, "S_B1", "sample", "Q", "Q", "libA", ipr="01", iprp="01")
        samples.link_to_project(cur, pid, a1)
        samples.link_to_project(cur, pid, b1)

        metadata.set_visit(cur, va, "bmi", 22.0)
        metadata.set_sample(cur, a1, "well", "A01")

        for sid, ftype, path, tier in [
            (a1, "fastq_r1", str(f_a1_r1), "archive"),
            (a1, "bam",      str(f_a1_bam), "archive"),
            (b1, "beer_norm", str(f_b1_norm), "work"),
        ]:
            cur.execute(
                "INSERT INTO sample_files "
                "(sample_id, file_type, file_path, storage_tier) "
                "VALUES (?, ?, ?, ?)",
                (sid, ftype, path, tier),
            )

    yield {
        "project_id": pid,
        "sample_names": {"a1": "S_A1", "b1": "S_B1"},
        "source_paths": {
            "fastq_r1": str(f_a1_r1),
            "bam": str(f_a1_bam),
            "beer_norm": str(f_b1_norm),
        },
    }
    with transaction() as cur:
        wipe_all(cur)


# --------------------------------------------------------------------------- #
# export_metadata_table
# --------------------------------------------------------------------------- #

def test_export_metadata_table_csv(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    out = tmp_path / "export"
    written = fetch.export_metadata_table(
        project_id=pid, output_dir=out, formats=("csv",),
    )
    assert "csv" in written
    assert written["csv"].exists()
    df = pd.read_csv(written["csv"])
    assert set(df["sample_name"]) == {"S_A1", "S_B1"}
    assert "bmi" in df.columns
    assert "well" in df.columns


def test_export_metadata_table_rejects_unknown_format(
    project_with_local_files, tmp_path,
):
    pid = project_with_local_files["project_id"]
    with pytest.raises(ValueError):
        fetch.export_metadata_table(
            project_id=pid, output_dir=tmp_path, formats=("parquet",),
        )


# --------------------------------------------------------------------------- #
# download_files_for_project (local-copy path, no SSH)
# --------------------------------------------------------------------------- #

def test_download_files_local_copy_all(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    out = tmp_path / "dl"
    # No SSH host configured → transport falls back to shutil.copyfile.
    report = fetch.download_files_for_project(
        project_id=pid,
        output_dir=out,
        config_path=None,
        ssh_host="",  # explicit empty override beats any env NOXDB_SSH_HOST
    )
    assert len(report["downloaded"]) == 3
    assert report["failed"] == []
    # by_sample layout
    assert (out / "S_A1" / "fastq_r1.fastq.gz").exists()
    assert (out / "S_A1" / "bam.bam").exists()
    assert (out / "S_B1" / "beer_norm.tsv").exists()


def test_download_files_filtered_by_type(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    out = tmp_path / "dl"
    report = fetch.download_files_for_project(
        project_id=pid,
        output_dir=out,
        file_types=["bam"],
        config_path=None,
        ssh_host="",
    )
    assert len(report["downloaded"]) == 1
    assert report["downloaded"][0]["file_path"].endswith("A1.bam")


def test_download_files_skips_existing(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    out = tmp_path / "dl"
    # First run: everything downloaded.
    fetch.download_files_for_project(
        project_id=pid, output_dir=out, config_path=None, ssh_host="",
    )
    # Second run: same destination exists → all skipped.
    report = fetch.download_files_for_project(
        project_id=pid, output_dir=out, config_path=None, ssh_host="",
    )
    assert report["downloaded"] == []
    assert len(report["skipped"]) == 3


def test_download_files_reports_missing_source(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    # Break one source path to provoke a copy failure.
    with transaction() as cur:
        cur.execute(
            "UPDATE sample_files SET file_path = ? WHERE file_type = ?",
            ("/nonexistent/missing.bam", "bam"),
        )
    report = fetch.download_files_for_project(
        project_id=pid, output_dir=tmp_path / "dl",
        config_path=None, ssh_host="",
    )
    assert len(report["failed"]) == 1
    assert "missing.bam" in report["failed"][0]["file_path"]
    assert len(report["downloaded"]) == 2


def test_download_files_by_type_layout(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    out = tmp_path / "dl"
    fetch.download_files_for_project(
        project_id=pid, output_dir=out, layout="by_type",
        config_path=None, ssh_host="",
    )
    assert (out / "bam" / "S_A1.bam").exists()
    assert (out / "fastq_r1" / "S_A1.fastq.gz").exists()
    assert (out / "beer_norm" / "S_B1.tsv").exists()


def test_download_files_rejects_unknown_layout(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    with pytest.raises(ValueError):
        fetch.download_files_for_project(
            project_id=pid, output_dir=tmp_path / "dl", layout="weird",
            config_path=None, ssh_host="",
        )


def test_download_files_empty_project(_init_pool, tmp_path):
    with transaction() as cur:
        wipe_all(cur)
        pid = projects.create(cur, "EMPTY_P")
    report = fetch.download_files_for_project(
        project_id=pid, output_dir=tmp_path / "dl",
        config_path=None, ssh_host="",
    )
    assert report["downloaded"] == []
    assert report["skipped"] == []
    assert report["failed"] == []
    with transaction() as cur:
        wipe_all(cur)


# --------------------------------------------------------------------------- #
# Tar members (schema 006), local path
# --------------------------------------------------------------------------- #

@pytest.fixture
def project_with_tar_members(_init_pool, tmp_path):
    """One sample whose R1/R2 FASTQs are members of a real tar."""
    import hashlib
    import tarfile

    src = tmp_path / "src"
    src.mkdir()
    payloads = {"S_T1_R1.fastq.gz": b"read-one" * 500, "S_T1_R2.fastq.gz": b"read-two" * 300}
    for name, data in payloads.items():
        (src / name).write_bytes(data)
    tar = tmp_path / "run.tar"
    with tarfile.open(tar, "w") as tf:
        for name in payloads:
            tf.add(src / name, arcname=name)
    with tarfile.open(tar) as tf:
        offsets = {m.name: m.offset_data for m in tf}

    with transaction() as cur:
        wipe_all(cur)
        pid = projects.create(cur, "TAR_P")
        sa = subjects.create(cur, "S_T", "F")
        va = visits.create(cur, sa, "ctrl", 30, timepoint="baseline")
        t1 = samples.create(cur, va, "S_T1", "sample", "Q", "Q", "libA", ipr="01", iprp="01")
        samples.link_to_project(cur, pid, t1)
        for name, ftype in [("S_T1_R1.fastq.gz", "fastq_r1"), ("S_T1_R2.fastq.gz", "fastq_r2")]:
            cur.execute(
                "INSERT INTO sample_files (sample_id, file_type, file_path, archive_member, "
                "archive_offset, file_size_bytes, checksum_md5, storage_tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'archive')",
                (t1, ftype, str(tar), name, offsets[name], len(payloads[name]),
                 hashlib.md5(payloads[name]).hexdigest()),
            )
    yield {"project_id": pid, "payloads": payloads}
    with transaction() as cur:
        wipe_all(cur)


def test_download_tar_members_by_offset(project_with_tar_members, tmp_path):
    info = project_with_tar_members
    out = tmp_path / "dl"
    report = fetch.download_files_for_project(
        project_id=info["project_id"], output_dir=out, config_path=None, ssh_host="",
    )
    assert report["failed"] == []
    assert (out / "S_T1" / "fastq_r1.fastq.gz").read_bytes() == info["payloads"]["S_T1_R1.fastq.gz"]
    assert (out / "S_T1" / "fastq_r2.fastq.gz").read_bytes() == info["payloads"]["S_T1_R2.fastq.gz"]
    assert {d["archive_member"] for d in report["downloaded"]} == set(info["payloads"])


def test_download_tar_member_without_offset_scans_tar(project_with_tar_members, tmp_path):
    info = project_with_tar_members
    with transaction() as cur:
        cur.execute("UPDATE sample_files SET archive_offset = NULL")
    out = tmp_path / "dl"
    report = fetch.download_files_for_project(
        project_id=info["project_id"], output_dir=out, layout="flat",
        config_path=None, ssh_host="",
    )
    assert report["failed"] == []
    for name, data in info["payloads"].items():
        assert (out / name).read_bytes() == data


def test_download_tar_member_md5_mismatch_fails_and_cleans_up(project_with_tar_members, tmp_path):
    info = project_with_tar_members
    with transaction() as cur:
        cur.execute(
            "UPDATE sample_files SET checksum_md5 = ? WHERE file_type = 'fastq_r1'", ("0" * 32,)
        )
    out = tmp_path / "dl"
    report = fetch.download_files_for_project(
        project_id=info["project_id"], output_dir=out, config_path=None, ssh_host="",
    )
    assert len(report["failed"]) == 1
    assert "md5 mismatch" in report["failed"][0]["error"]
    assert not (out / "S_T1" / "fastq_r1.fastq.gz").exists()
    assert len(report["downloaded"]) == 1


# --------------------------------------------------------------------------- #
# SFTP path — paramiko mocked
# --------------------------------------------------------------------------- #

def test_download_files_uses_sftp_when_ssh_host_set(
    project_with_local_files, tmp_path, monkeypatch,
):
    """When ssh_host is configured, transport opens paramiko SFTP and uses
    its .get() to fetch each file. We monkeypatch paramiko at module level
    to capture calls without needing a real SSH server."""
    paramiko = pytest.importorskip("paramiko")

    calls: list[tuple[str, str]] = []

    class FakeSFTP:
        def get(self, remote, local):
            calls.append((remote, local))
            # Write a stub locally so dst.stat().st_size works.
            with open(local, "wb") as f:
                f.write(b"stub")

        def close(self):
            pass

    class FakeClient:
        def __init__(self) -> None:
            self.connected = False

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            self.connected = True

        def open_sftp(self):
            return FakeSFTP()

        def close(self):
            pass

    monkeypatch.setattr(paramiko, "SSHClient", FakeClient)

    pid = project_with_local_files["project_id"]
    out = tmp_path / "dl_sftp"
    report = fetch.download_files_for_project(
        project_id=pid,
        output_dir=out,
        config_path=None,
        ssh_host="fake.example.com",
        ssh_user="someone",
    )
    assert len(calls) == 3
    assert len(report["downloaded"]) == 3
    assert report["failed"] == []


def test_download_tar_members_over_sftp(project_with_tar_members, tmp_path, monkeypatch):
    """Tar members are read through sftp.open() (seek + read), never .get()."""
    paramiko = pytest.importorskip("paramiko")
    opened: list[str] = []

    class FakeSFTP:
        def open(self, remote, mode):
            opened.append(remote)
            return open(remote, mode)  # the tar is local in the test

        def get(self, remote, local):
            raise AssertionError("tar members must not be fetched with get()")

        def close(self):
            pass

    class FakeClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            pass

        def open_sftp(self):
            return FakeSFTP()

        def close(self):
            pass

    monkeypatch.setattr(paramiko, "SSHClient", FakeClient)
    info = project_with_tar_members
    out = tmp_path / "dl_sftp"
    report = fetch.download_files_for_project(
        project_id=info["project_id"], output_dir=out, config_path=None,
        ssh_host="fake.example.com", ssh_user="someone",
    )
    assert report["failed"] == []
    assert len(opened) == 2
    assert (out / "S_T1" / "fastq_r1.fastq.gz").read_bytes() == info["payloads"]["S_T1_R1.fastq.gz"]


# --------------------------------------------------------------------------- #
# export_project
# --------------------------------------------------------------------------- #

def test_export_project_end_to_end(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    out = tmp_path / "snapshot"
    report = fetch.export_project(
        project_id=pid,
        output_dir=out,
        config_path=None,
        ssh_host="",
    )
    assert (out / "metadata.csv").exists()
    assert (out / "README.txt").exists()
    assert (out / "files" / "S_A1" / "bam.bam").exists()
    readme = (out / "README.txt").read_text(encoding="utf-8")
    assert "FETCH_P" in readme
    assert "samples:  2" in readme
    assert report["summary"]["n_files"] == 3


def test_export_project_metadata_only(project_with_local_files, tmp_path):
    pid = project_with_local_files["project_id"]
    out = tmp_path / "snapshot_meta"
    fetch.export_project(
        project_id=pid,
        output_dir=out,
        include_files=False,
        config_path=None,
        ssh_host="",
    )
    assert (out / "metadata.csv").exists()
    assert not (out / "files").exists()
