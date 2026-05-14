# Quickstart

This guide is for **read-only users** — researchers who want to query the
database, explore projects, and pull data into pandas. It assumes you have
already [installed the package](install.md) and configured `~/.my.cnf`.

---

## 1. Connecting to the database

The CCR database lives inside the LiSC network and is only reachable directly
when you are on-site or on the VPN. From outside, you must tunnel through the
SSH gateway first.

### Option A — On-site / VPN (direct)

`init_pool()` connects straight to the DB host configured in `~/.my.cnf`.
Nothing else is needed:

```python
from dbmaria_utils import init_pool, close_pool

init_pool()
# ... your queries ...
close_pool()
```

### Option B — Remote (SSH tunnel)

The database is not directly reachable from outside LiSC. You need to open a
local port-forwarding tunnel through the SSH gateway first, then tell
`init_pool()` to connect through it.

#### Step 1 — Add `local_port` to `~/.my.cnf`

In the `[labdb-ssh]` section, add the local port the tunnel will bind to:

```ini
[labdb-ssh]
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

Replace `<host>` with the value of `host` from the `[labdb]` section of your
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
from dbmaria_utils import init_pool, close_pool

init_pool()   # detects 127.0.0.1:3307 is listening and connects through it
# ... your queries ...
close_pool()
```

`init_pool()` checks whether `local_port` (3307) is already bound. If it is,
it connects through the existing tunnel without opening a new one. If it is
not, it raises an error — which is intentional: you should always start the
tunnel explicitly so you know it is running.

#### Killing the tunnel when you are done

```bash
pkill -f "L 3307:<host>:3306"
```

---

## 2. Listing all projects

```python
from dbmaria_utils import projects, transaction

with transaction() as cur:
    proj_list = projects.list_all(cur)
```

| project_id | project_name       | description                                              |
|------------|--------------------|----------------------------------------------------------|
| 7          | ADMCI_NED          |                                                          |
| 10         | BAT_BATIOS_Kiefer  | BAT (n=62) + BATIOS (n=74), 136 serum samples            |
| 13         | BC-Engl            | bladder cancer: Cis (n=141), Carbo (n=47), RCE (n=126)  |
| 19         | CRC_radiotherapy   | diff. timepoints, 320 samples                            |
| 25         | HCC_MUW            | 150 + 30 (TKI therapy) + 78 HCs + 48 TKI-treated        |
| …          | …                  | …                                                        |

17 projects total.

---

## 3. Project summary

```python
from dbmaria_utils import queries

with transaction() as cur:
    summary = queries.project_summary(cur, project_id=7)
```

```json
{
  "project_id": 7,
  "n_subjects": 108,
  "n_visits": 108,
  "n_samples": 108,
  "n_files": 216,
  "files_by_type": {
    "counts": 108,
    "zigp_norm": 108
  }
}
```

---

## 4. Samples for a project

The main query for pulling all samples belonging to a project. Returns a flat
`DataFrame` joining subjects → visits → samples in three parameterized
single-table lookups.

```python
with transaction() as cur:
    df = queries.samples_for_project(cur, project_id=7)
```

| project_id | subject_id | subject_code                       | visit_id | timepoint | sample_id | sample_name                        | sample_type | SQR | SQRP | library |
|------------|------------|------------------------------------|----------|-----------|-----------|------------------------------------|-------------|-----|------|---------|
| 7          | 649        | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | 649      | baseline  | 649       | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | sample      | 07  | 02   | A_T_C2  |
| 7          | 652        | R14P02_74_FAU0002_ADMCI_NED_A_T_C2 | 652      | baseline  | 652       | R14P02_74_FAU0002_ADMCI_NED_A_T_C2 | sample      | 07  | 02   | A_T_C2  |
| 7          | 655        | R19P04_14_MG0213_ADMCI_NED_A_T_C2  | 655      | baseline  | 655       | R19P04_14_MG0213_ADMCI_NED_A_T_C2  | sample      | 14  | 05   | A_T_C2  |
| …          | …          | …                                  | …        | …         | …         | …                                  | …           | …   | …    | …       |

108 rows total.

### Filtering by file presence

```python
with transaction() as cur:
    df_with    = queries.samples_for_project(cur, project_id=7, has_files=True)
    df_without = queries.samples_for_project(cur, project_id=7, has_files=False)
```

---

## 5. Subjects

```python
from dbmaria_utils import subjects

with transaction() as cur:
    subj_list = subjects.list_for_project(cur, project_id=7)
```

```json
{
  "subject_id": 649,
  "project_id": 7,
  "subject_code": "R14P02_77_FAU0001_ADMCI_NED_A_T_C2",
  "sex": "F",
  "origin": "Netherlands",
  "created_at": "2026-05-14T12:27:07"
}
```

108 subjects in this project.

---

## 6. Visits

```python
with transaction() as cur:
    cur.execute("SELECT * FROM visits WHERE subject_id = ? LIMIT 1", (649,))
```

```json
{
  "visit_id": 649,
  "subject_id": 649,
  "timepoint": "baseline",
  "group_test": "Controls",
  "age": 83,
  "created_at": "2026-05-14T12:27:13"
}
```

---

## 7. Sample detail

```python
from dbmaria_utils import samples

with transaction() as cur:
    s = samples.get(cur, sample_id=649)
```

```json
{
  "sample_id": 649,
  "visit_id": 649,
  "sample_name": "R14P02_77_FAU0001_ADMCI_NED_A_T_C2",
  "sample_type": "sample",
  "SQR": "07",
  "SQRP": "02",
  "library": "A_T_C2",
  "antibody_class": null,
  "created_at": "2026-05-14T12:27:18"
}
```

---

## 8. Samples with metadata

Equivalent to `samples_for_project` but includes all EAV metadata columns
joined in:

```python
with transaction() as cur:
    dfm = queries.samples_with_metadata(cur, project_id=7)
```

| project_id | subject_id | subject_code                       | visit_id | timepoint | sample_id | sample_name                        | sample_type | SQR | SQRP | library | antibody_class |
|------------|------------|------------------------------------|----------|-----------|-----------|------------------------------------|-------------|-----|------|---------|----------------|
| 7          | 649        | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | 649      | baseline  | 649       | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | sample      | 07  | 02   | A_T_C2  | None           |
| 7          | 652        | R14P02_74_FAU0002_ADMCI_NED_A_T_C2 | 652      | baseline  | 652       | R14P02_74_FAU0002_ADMCI_NED_A_T_C2 | sample      | 07  | 02   | A_T_C2  | None           |
| 7          | 655        | R19P04_14_MG0213_ADMCI_NED_A_T_C2  | 655      | baseline  | 655       | R19P04_14_MG0213_ADMCI_NED_A_T_C2  | sample      | 14  | 05   | A_T_C2  | None           |
| …          | …          | …                                  | …        | …         | …         | …                                  | …           | …   | …    | …       | …              |

108 rows × 12 columns.

---

## 9. Files for a project

```python
with transaction() as cur:
    dff = queries.files_for_project(cur, project_id=7)
```

| file_id | sample_id | sample_name                        | subject_code                       | timepoint | file_type | file_path                                                          | storage_tier |
|---------|-----------|------------------------------------|------------------------------------|-----------|-----------|---------------------------------------------------------------------|--------------|
| 226     | 649       | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | baseline  | counts    | /lisc/data/work/ccr/counts/R14P02_77_FAU0001_ADMCI_NED_A_T_C2.count.gz | work     |
| 229     | 649       | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | baseline  | zigp_norm | /lisc/data/work/ccr/zigp/R14P02_77_FAU0001_ADMCI_NED_A_T_C2.csv       | work     |
| 232     | 652       | R14P02_74_FAU0002_ADMCI_NED_A_T_C2 | R14P02_74_FAU0002_ADMCI_NED_A_T_C2 | baseline  | counts    | /lisc/data/work/ccr/counts/R14P02_74_FAU0002_ADMCI_NED_A_T_C2.count.gz | work    |
| …       | …         | …                                  | …                                  | …         | …         | …                                                                   | …            |

216 files total (108 `counts` + 108 `zigp_norm`).

---

## 10. Project tidy table

A single wide DataFrame joining all levels (project → subject → visit →
sample) with metadata pivoted into columns. The standard starting point for
downstream analysis:

```python
with transaction() as cur:
    dft = queries.project_tidy_table(cur, project_id=7)
```

| project_id | subject_id | subject_code                       | visit_id | timepoint | sample_id | sample_name                        | sample_type | SQR | SQRP | library | antibody_class |
|------------|------------|------------------------------------|----------|-----------|-----------|------------------------------------|-------------|-----|------|---------|----------------|
| 7          | 649        | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | 649      | baseline  | 649       | R14P02_77_FAU0001_ADMCI_NED_A_T_C2 | sample      | 07  | 02   | A_T_C2  | None           |
| …          | …          | …                                  | …        | …         | …         | …                                  | …           | …   | …    | …       | …              |

Shape: 108 rows × 12 columns.

---

## 11. Shut down

Always close the pool when you are done:

```python
close_pool()
```

In scripts, use a `try/finally` to guarantee cleanup even if a query fails:

```python
try:
    init_pool()
    with transaction() as cur:
        df = queries.samples_for_project(cur, project_id=7)
finally:
    close_pool()
```

---

## Where to go next

- [API reference](reference/index.md) — every public function.
- [Schema](schema.md) — the table layout.
- [Install](install.md) — prerequisites and `~/.my.cnf` setup.
