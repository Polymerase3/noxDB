# Quickstart

This guide is for **read-only users** — researchers who want to query the
database, explore projects, and pull data into pandas. It assumes you have
already [installed the package](install.md) and configured `~/.my.cnf`.

---

## 1. Connecting to the database

The CCR database lives inside the LiSC network and is only reachable directly
when you are on-site or on the VPN. From outside, you must tunnel through the
SSH gateway first.

> **⚠ Prefer Option B (SSH tunnel) when working remotely.**
>
> Option A makes a direct TCP connection to the database host on every
> `init_pool()` call. Opening and closing that connection repeatedly from
> outside the LiSC network will trigger **fail2ban** on the SSH gateway and
> get your IP temporarily banned. Always use Option B and keep the tunnel
> open for your entire session — it opens once and stays alive.

### Option A — On-site / VPN (direct)

`init_pool()` connects straight to the DB host configured in `~/.my.cnf`.
Nothing else is needed:

```python
from noxdb import init_pool, close_pool

init_pool()
# ... your queries ...
close_pool()
```

### Option B — Remote (SSH tunnel)

The database is not directly reachable from outside LiSC. You need to open a
local port-forwarding tunnel through the SSH gateway first, then tell
`init_pool()` to connect through it.

#### Step 1 — Add `local_port` to `~/.my.cnf`

In the `[noxdb-ssh]` section, add the local port the tunnel will bind to:

```ini
[noxdb-ssh]
ssh_host = ccr-lab.lisc.univie.ac.at
ssh_user = youruser
local_port = 3307
```

This is how `init_pool()` knows which port to look for.

#### Step 2 — Open the tunnel (once per session)

Run this in your terminal before starting any Python session:

```bash
ssh -f -N -L 3307:<host>:3306 youruser@ccr-lab.lisc.univie.ac.at
```

Replace `<host>` with the value of `host` from the `[noxdb]` section of your
`~/.my.cnf`. The `-f` flag backgrounds the process; `-N` means no remote
command is run — the tunnel just stays open.

#### Step 3 — Verify the tunnel is alive

```bash
ss -tlnp | grep 3307
```

You should see a line like:

```
LISTEN  0  128  127.0.0.1:3307  0.0.0.0:*
```

If the output is empty, the tunnel is not running. Re-run the `ssh` command.

#### Step 4 — Connect from Python

```python
from noxdb import init_pool, close_pool

init_pool()   # detects 127.0.0.1:3307 is listening and connects through it
# ... your queries ...
close_pool()
```

`init_pool()` checks whether `local_port` (3307) is already bound. If it is,
it connects through the existing tunnel without opening a new one. If it is
not, and `[noxdb-ssh]` gives an `ssh_host`, it opens its own tunnel by running
`ssh -N -L` in the background and closes it again on `close_pool()`. Starting the tunnel
yourself is still the recommended way, because one long-lived tunnel is
easier to see and to kill than one opened per session.

#### Killing the tunnel when you are done

```bash
pkill -f "L 3307:<host>:3306"
```

---

## 2. Listing all projects

The outputs on this page are **invented example data** (a fictional
`IBD_Vienna` project, the same one used in
[Data preparation](data-preparation.md)). They show the shape of what each
call returns, not what is in the database.

```python
from noxdb import projects, transaction

with transaction() as cur:
    proj_list = projects.list_all(cur)
```

| project_id | project_name | description                                                   |
|------------|--------------|---------------------------------------------------------------|
| 3          | input        | Control samples (input DNA)                                   |
| 12         | IBD_Vienna   | UC (n=40), CD (n=30), HC (n=20) — serum samples collected at MUW |
| 14         | RA_Example   | rheumatoid arthritis, baseline + month 6                      |
| …          | …            | …                                                             |

One row per study project, plus the `input` umbrella project.
The dedicated `mockIP` / `anchor` / `NC` projects were removed in
migration `003`; those controls are now linked to the study projects
that share their plate (see [§11](#11-plate-controls-for-a-project)).

---

## 3. Project summary

```python
from noxdb import queries

with transaction() as cur:
    summary = queries.project_summary(cur, project_id=12)
```

```json
{
  "project_id": 12,
  "n_subjects": 122,
  "n_visits": 192,
  "n_samples": 192,
  "n_files": 960,
  "files_by_type": {
    "fastq_r1": 192,
    "fastq_r2": 192,
    "counts": 192,
    "zigp_norm": 192,
    "zigp_loose": 192
  },
  "n_controls": 32,
  "controls_by_type": {
    "mockIP": 16,
    "anchor": 8,
    "NC": 8
  }
}
```

`n_samples` counts **every** sample linked to the project via
`project_samples`, controls included — here 160 study + 32 controls =
192. `n_controls` / `controls_by_type` break out just the control
subset (matched onto the project's IP plates by IPR + IPRP at import /
migration time).

---

## 4. Samples for a project

The main query for pulling all samples belonging to a project. Real
samples and their plate controls (mockIP, anchor, NC) are all project
members via `project_samples`, returned in one flat `DataFrame`. The
`project_id` column is always the queried project (`12` for every row,
controls included) — tell rows apart by `sample_type`.

```python
with transaction() as cur:
    df = queries.samples_for_project(cur, project_id=12)
```

| project_id | subject_id | subject_code            | visit_id | timepoint | sample_id | sample_name                     | sample_type | IPR | IPRP | SQR | SQRP | library | antibody_class |
|------------|------------|-------------------------|----------|-----------|-----------|---------------------------------|-------------|-----|------|-----|------|---------|----------------|
| 12         | 1001       | IBD_VIE_001             | 2001     | baseline  | 3001      | R25P01_01_IBD001_IBD_VIE_A_T_C2 | sample      | 25  | 01   | 12  | 03   | A_T_C2  | None           |
| 12         | 1001       | IBD_VIE_001             | 2002     | week12    | 3002      | R25P01_02_IBD002_IBD_VIE_A_T_C2 | sample      | 25  | 01   | 12  | 03   | A_T_C2  | None           |
| …          | …          | …                       | …        | …         | …         | …                               | …           | …   | …    | …   | …    | …       | …              |
| 12         | 1101       | R25P01_81_Mock_1_A_T_C2 | 2161     | baseline  | 3161      | R25P01_81_Mock_1_A_T_C2         | mockIP      | 25  | 01   | 12  | 03   | A_T_C2  | None           |
| …          | …          | …                       | …        | …         | …         | …                               | …           | …   | …    | …   | …    | …       | …              |

`IPR`/`IPRP` are the immunoprecipitation plate, from the "Overview of
IP runs" sheet; on some plates they differ from the `RxxPxx` in the
name. `SQR`/`SQRP` are the sequencing plate, which comes from the run
sheet. They are different numbers for the same row (here IP plate 25/01,
sequencing plate 12/03). See
[Two coordinate systems](schema.md#two-coordinate-systems).

192 rows total (160 `sample` + 16 `mockIP` + 8 `anchor` + 8 `NC`).

### Real samples only

```python
with transaction() as cur:
    df = queries.samples_for_project(cur, project_id=12, include_controls=False)
```

160 rows.

### Filtering by file presence

```python
with transaction() as cur:
    df_with    = queries.samples_for_project(cur, project_id=12, has_files=True)
    df_without = queries.samples_for_project(cur, project_id=12, has_files=False)
```

192 with files, 0 without (file filter applies to both real samples and controls).

---

## 5. Subjects

```python
from noxdb import subjects

with transaction() as cur:
    subj_list = subjects.list_for_project(cur, project_id=12)
```

```json
{
  "subject_id": 1001,
  "subject_code": "IBD_VIE_001",
  "sex": "F",
  "origin": "Austria",
  "created_at": "2026-01-15T10:00:00"
}
```

122 subjects. `subjects` no longer carries a `project_id` (dropped in
migration `003`); `list_for_project` now traverses
`project_samples → samples → visits → subjects`, so every subject with
a sample in the project — control subjects included — is returned.

---

## 6. Visits

```python
with transaction() as cur:
    cur.execute("SELECT * FROM visits WHERE subject_id = ? LIMIT 1", (1001,))
```

```json
{
  "visit_id": 2001,
  "subject_id": 1001,
  "timepoint": "baseline",
  "group_test": "UC",
  "age": 34,
  "created_at": "2026-01-15T10:00:01"
}
```

---

## 7. Sample detail

```python
from noxdb import samples

with transaction() as cur:
    s = samples.get(cur, sample_id=3001)
```

```json
{
  "sample_id": 3001,
  "visit_id": 2001,
  "sample_name": "R25P01_01_IBD001_IBD_VIE_A_T_C2",
  "sample_type": "sample",
  "IPR": "25",
  "IPRP": "01",
  "SQR": "12",
  "SQRP": "03",
  "library": "A_T_C2",
  "antibody_class": null,
  "created_at": "2026-01-15T10:00:02"
}
```

---

## 8. Samples with metadata

Equivalent to `samples_for_project` but includes all EAV metadata columns
joined in. Includes controls by default.

```python
with transaction() as cur:
    dfm = queries.samples_with_metadata(cur, project_id=12)
```

192 rows × 17 columns:

```
['project_id', 'subject_id', 'subject_code', 'visit_id', 'timepoint',
 'sample_id', 'sample_name', 'sample_type', 'IPR', 'IPRP', 'SQR', 'SQRP',
 'library', 'antibody_class', 'treatment', 'CRP', 'disease_activity']
```

---

## 9. Files for a project

Returns every file registered for samples linked to the project —
study samples **and** controls, since both are project members via
`project_samples`.

```python
with transaction() as cur:
    dff = queries.files_for_project(cur, project_id=12)
```

| file_id | sample_id | sample_name                     | subject_code | timepoint | file_type | file_path                                                                            | archive_member                                     | archive_offset | file_size_bytes | checksum_md5                     | storage_tier | created_at          |
|---------|-----------|---------------------------------|--------------|-----------|-----------|--------------------------------------------------------------------------------------|----------------------------------------------------|----------------|-----------------|----------------------------------|--------------|---------------------|
| 5001    | 3001      | R25P01_01_IBD001_IBD_VIE_A_T_C2 | IBD_VIE_001  | baseline  | counts    | /lisc/data/work/ccr/mariaDB/counts/R25P01_01_IBD001_IBD_VIE_A_T_C2.count.gz           |                                                    | None           | None            | None                             | work         | 2026-01-15 10:00:03 |
| 5002    | 3001      | R25P01_01_IBD001_IBD_VIE_A_T_C2 | IBD_VIE_001  | baseline  | zigp_norm | /lisc/data/work/ccr/mariaDB/zigp/R25P01_01_IBD001_IBD_VIE_A_T_C2.csv                  |                                                    | None           | None            | None                             | work         | 2026-01-15 10:00:03 |
| 5004    | 3001      | R25P01_01_IBD001_IBD_VIE_A_T_C2 | IBD_VIE_001  | baseline  | fastq_r1  | /lisc/archive/ccr/mariaDB/fastq_tar/SQR12_fastq_files.tar                             | R25P01_01_IBD001_IBD_VIE_A_T_C2_R1.fastq.gz        | 512            | 148213760       | 0123456789abcdef0123456789abcdef | archive      | 2026-01-15 10:00:04 |
| …       | …         | …                               | …            | …         | …         | …                                                                                    | …                                                  | …              | …               | …                                | …            | …                   |

FASTQs are stored inside one tar per sequencing run: `file_path` is the
tar, `archive_member` the file's name inside it and `archive_offset` the
byte position of its data. Plain files have an empty `archive_member`.

960 files total (192 each of `counts`, `zigp_norm`, `zigp_loose`,
`fastq_r1` and `fastq_r2`).

---

## 10. Project tidy table

A single wide DataFrame: the `subject → visit → sample` lineage joined
to project membership (`project_samples`) with metadata pivoted into
columns. Includes controls. The standard starting point for downstream
analysis:

```python
with transaction() as cur:
    dft = queries.project_tidy_table(cur, project_id=12)
```

Shape: 192 rows × 17 columns, the same set as §8.

---

## 11. Plate controls for a project

Controls (mockIP, anchor, NC) have no project of their own — they are
linked to every study project sharing their IP plate through
`project_samples` (done at import / migration time, matched by
IPR + IPRP). Because a plate can span multiple projects the same
control appears for several projects: one underlying row, many
membership links. The `project_id` column is therefore the queried
project.

```python
with transaction() as cur:
    dfc = queries.controls_for_project(cur, project_id=12)
```

```
sample_type
NC         8
anchor     8
mockIP    16
```

| sample_id | sample_name              | sample_type | IPR | IPRP | SQR | SQRP | library | project_id |
|-----------|--------------------------|-------------|-----|------|-----|------|---------|------------|
| 3161      | R25P01_81_Mock_1_A_T_C2  | mockIP      | 25  | 01   | 12  | 03   | A_T_C2  | 12         |
| 3162      | R25P01_82_Mock_2_A_T_C2  | mockIP      | 25  | 01   | 12  | 03   | A_T_C2  | 12         |
| …         | …                        | …           | …   | …    | …   | …    | …       | …          |

32 controls total (16 `mockIP` + 8 `anchor` + 8 `NC`).

### Filtering by type

```python
with transaction() as cur:
    mocks   = queries.controls_for_project(cur, project_id=12, sample_types=["mockIP"])
    anchors = queries.controls_for_project(cur, project_id=12, sample_types=["anchor"])
    qc      = queries.controls_for_project(cur, project_id=12, sample_types=["anchor", "NC"])
```

---

## 12. Input samples

Input DNA samples are not associated with any study project. Use
`list_inputs()` to retrieve all of them globally:

```python
with transaction() as cur:
    dfi = queries.list_inputs(cur)
```

| sample_id | sample_name         | sample_type | IPR | IPRP | SQR | SQRP | library | project_id |
|-----------|---------------------|-------------|-----|------|-----|------|---------|------------|
| 3901      | R25_input_01_A_T_C2 | input       | 25  |      | 12  |      | A_T_C2  | 3          |
| 3902      | R25_input_02_A_T_C2 | input       | 25  |      | 12  |      | A_T_C2  | 3          |
| …         | …                   | …           | …   | …    | …   | …    | …       | …          |

One row per input library, all in the `input` umbrella project. Input
series are named `R<IP run>_input_<nn>_<library>`.

---

## 13. Shut down

Always close the pool when you are done:

```python
close_pool()
```

In scripts, use a `try/finally` to guarantee cleanup even if a query fails:

```python
try:
    init_pool()
    with transaction() as cur:
        df = queries.samples_for_project(cur, project_id=12)
finally:
    close_pool()
```

---

## 14. Full fetch example

The `fetch` module is the consumer side of noxdb: given a project, it
materialises the project structure, a tidy metadata table, and — when running
on LiSC or through an SFTP jump host — the actual files.

### Project structure and file manifest

```python
from noxdb import init_pool, close_pool, transaction
from noxdb import projects, queries

try:
    init_pool()
    with transaction() as cur:
        project_row = projects.get(cur, project_id=12)
        summary     = queries.project_summary(cur, project_id=12)
        dff         = queries.files_for_project(cur, project_id=12)
finally:
    close_pool()
```

`project_row`:

```json
{
  "project_id": 12,
  "project_name": "IBD_Vienna",
  "description": "UC (n=40), CD (n=30), HC (n=20) — serum samples collected at MUW",
  "pi_name": "Dr. Jane Doe",
  "created_at": "2026-01-15T10:00:00"
}
```

`summary`: the same dictionary as in [§3](#3-project-summary).

`dff` — first 6 rows of 960:

```
 file_id                      sample_name   file_type                                                                   file_path storage_tier
    5001  R25P01_01_IBD001_IBD_VIE_A_T_C2      counts   /lisc/data/work/ccr/mariaDB/counts/R25P01_01_IBD001_IBD_VIE_A_T_C2.count.gz         work
    5002  R25P01_01_IBD001_IBD_VIE_A_T_C2   zigp_norm          /lisc/data/work/ccr/mariaDB/zigp/R25P01_01_IBD001_IBD_VIE_A_T_C2.csv         work
    5003  R25P01_01_IBD001_IBD_VIE_A_T_C2  zigp_loose /lisc/data/work/ccr/mariaDB/zigp_loose_cutoff/R25P01_01_IBD001_IBD_VIE_A_T_C2.csv.gz  work
    5004  R25P01_01_IBD001_IBD_VIE_A_T_C2    fastq_r1                     /lisc/archive/ccr/mariaDB/fastq_tar/SQR12_fastq_files.tar      archive
    5005  R25P01_01_IBD001_IBD_VIE_A_T_C2    fastq_r2                     /lisc/archive/ccr/mariaDB/fastq_tar/SQR12_fastq_files.tar      archive
    5006  R25P01_02_IBD002_IBD_VIE_A_T_C2      counts   /lisc/data/work/ccr/mariaDB/counts/R25P01_02_IBD002_IBD_VIE_A_T_C2.count.gz         work
```

### Exporting to a local folder

`fetch.export_project` combines metadata, a README, and (optionally) the
actual files into a single output directory. Pass `include_files=False` to
get just the metadata and README without downloading:

```python
from noxdb import init_pool, close_pool, transaction
from noxdb import fetch

try:
    init_pool()
    with transaction() as cur:
        result = fetch.export_project(
            cur,
            project_id=12,
            output_dir="exports/IBD_Vienna",
            include_files=False,          # set True on LiSC to also pull files
            metadata_formats=("csv",),
        )
finally:
    close_pool()
```

Output directory layout:

```
exports/IBD_Vienna/
├── README.txt     (a few hundred bytes)
└── metadata.csv   (one row per sample)
```

`README.txt`:

```
Project: IBD_Vienna (id=12)
PI: Dr. Jane Doe
Description: UC (n=40), CD (n=30), HC (n=20) — serum samples collected at MUW
Created: 2026-01-15 10:00:00

Counts:
  subjects: 122
  visits:   192
  samples:  192
  files:    960

Files by type:
  counts: 192
  fastq_r1: 192
  fastq_r2: 192
  zigp_loose: 192
  zigp_norm: 192
```

`result` (the return value):

```json
{
  "project":  { "project_id": 12, "project_name": "IBD_Vienna", ... },
  "summary":  { "n_subjects": 122, "n_files": 960, ... },
  "metadata": { "csv": "exports/IBD_Vienna/metadata.csv" },
  "files":    { "downloaded": [], "skipped": [], "failed": [], "output_dir": null },
  "readme":   "exports/IBD_Vienna/README.txt",
  "output_dir": "exports/IBD_Vienna"
}
```

To also download the actual files (requires running on LiSC or an active SFTP
connection through the SSH gateway):

```python
result = fetch.export_project(
    cur,
    project_id=12,
    output_dir="exports/IBD_Vienna",
    include_files=True,
    file_types=["counts"],        # omit to get all types
    layout="by_sample",           # or 'by_type' / 'flat'
)
```

The `result["files"]` key then contains `downloaded`, `skipped`, and `failed`
lists so you can see exactly what was fetched and what failed.

---

## Where to go next

- [API reference](reference/index.md) — every public function.
- [Schema](schema.md) — the table layout.
- [Install](install.md) — prerequisites and `~/.my.cnf` setup.
