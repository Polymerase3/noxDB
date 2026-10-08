# Install

## Requirements

Before you start, make sure you have all of the following:

- **[Mamba](https://mamba.readthedocs.io) or conda** (e.g. via [Miniforge](https://github.com/conda-forge/miniforge)) — check with `mamba --version` or `conda --version`. This is the recommended way to get Python and the MariaDB driver; see [without conda](#without-conda) if you can't use it.
- **Access to the LiSC network** — one of:
    - you are on-site at LiSC or connected to the university VPN, or
    - you have unlocked your current IP address on the [LiSC firewall page](https://lisc.univie.ac.at/firewall/) (access lasts 12 hours; no VPN needed).
- **An SSH key for `ccr-lab.lisc.univie.ac.at`** — needed when you work from your own laptop (see Step 4).
- **Git** — to clone the repository (`git --version` should return something)

---

## Step 1 — Create the environment

The Python `mariadb` driver is not pure Python: it needs the MariaDB C client library. Conda-forge ships both prebuilt, so nothing has to be compiled and the versions match. This works the same on Linux, macOS and Windows:

```bash
mamba create -n noxdb -c conda-forge \
    python=3.12 \
    mariadb-connector-c \
    mariadb
mamba activate noxdb
```

(With conda, replace `mamba` by `conda`.) Activate the environment with `mamba activate noxdb` in every new terminal before you use noxdb.

??? note "Without conda"

    You need Python 3.10 or newer (`python3 --version`). Install the MariaDB client library from your system, then create a virtual environment: `python3 -m venv .venv && source .venv/bin/activate`. `pip install` in Step 2 then builds the driver against the library.

    === "Linux (Debian / Ubuntu)"

        ```bash
        sudo apt update
        sudo apt install libmariadb-dev
        ```

    === "Linux (Fedora / RHEL / Rocky)"

        ```bash
        sudo dnf install mariadb-devel
        ```

    === "macOS"

        You need [Homebrew](https://brew.sh).

        ```bash
        brew install mariadb-connector-c
        export CFLAGS="-I$(brew --prefix mariadb-connector-c)/include"
        export LDFLAGS="-L$(brew --prefix mariadb-connector-c)/lib"
        ```

        Add the two `export` lines to your `~/.zshrc` (or `~/.bash_profile`) if you want them to persist across terminal sessions.

    === "Windows"

        1. Download the **MariaDB Connector/C** installer from the [official MariaDB downloads page](https://mariadb.com/downloads/connectors/).
        2. Run the installer and follow the prompts. The default installation path is fine.
        3. Restart your terminal (or PowerShell) so the new paths are picked up.

---

## Step 2 — Get the package

**Clone the repository** from GitHub. Open a terminal, navigate to wherever you keep your code, and run:

```bash
git clone https://github.com/Polymerase3/noxdb.git
```

This creates a `noxdb/` folder. Go into it:

```bash
cd noxdb
```

**Install the package and its dependencies** into the active `noxdb` environment. We install it in "editable" mode (`-e`) so that any local changes you make are picked up immediately without reinstalling. The `analysis` extras add pandas and Excel support for pulling tables into data frames:

```bash
pip install -e ".[analysis]"
```

If you only need the core library, `pip install -e .` is enough.

---

## Step 3 — Set up your database credentials

Connection settings are stored in a plain-text config file called `~/.my.cnf` in your home directory. The library reads the `[noxdb]` section automatically every time it connects.

**Create or open the file:**

```bash
# On Linux / macOS
nano ~/.my.cnf

# On Windows (PowerShell)
notepad $HOME\.my.cnf
```

**Add the following block** (replace the placeholders with the user name and password you received from an admin):

```ini
[noxdb]
host=mariadb.lisc
port=3306
user=<your-db-username>
password=<your-db-password>
database=ccr_metadata
```

What each field means:

| Field      | What to put there                                                  |
|------------|--------------------------------------------------------------------|
| `host`     | Leave this as `mariadb.lisc` (the LiSC database server)            |
| `port`     | Leave this as `3306` unless told otherwise                         |
| `user`     | Your personal database username — provided by an admin             |
| `password` | Your database password — provided by an admin                      |
| `database` | Leave this as `ccr_metadata`                                    |

**Save the file**, then lock down its permissions so only you can read it:

```bash
# Linux / macOS only
chmod 600 ~/.my.cnf
```

---

## Step 4 — Set up SSH credentials

When you work from your own computer, noxdb talks to LiSC through the lab's VM (`ccr-lab.lisc.univie.ac.at`) over SSH, for two things:

- **Database queries** go through an SSH tunnel that noxdb opens for you.
- **File downloads** (`download_files_for_project`, `export_project`) are copied over SFTP.

You need [network access to LiSC](#requirements) for this, and you need to tell noxdb how to log in to the VM.

**Add a second section** to the same `~/.my.cnf` file, directly below `[noxdb]`:

```ini
[noxdb-ssh]
ssh_host=ccr-lab.lisc.univie.ac.at
ssh_user=<your-lisc-username>
ssh_pkey=~/.ssh/id_ed25519
```

What each field means:

| Field        | What to put there                                                                 |
|--------------|-----------------------------------------------------------------------------------|
| `ssh_host`   | The jump host — always `ccr-lab.lisc.univie.ac.at`                               |
| `ssh_user`   | Your LiSC username (the one you use to SSH into the cluster)                     |
| `ssh_pkey`   | Path to your **private** SSH key — usually `~/.ssh/id_ed25519` or `~/.ssh/id_rsa` |

The tunnel is opened with the OpenSSH `ssh` command, which must log in **without a prompt**. That means an SSH key: either one without a passphrase, or one whose passphrase is held by `ssh-agent` (`ssh-add ~/.ssh/id_ed25519`). Password login does not work for the tunnel. Run `ssh <your-lisc-username>@ccr-lab.lisc.univie.ac.at` once by hand first, so the host key is in `~/.ssh/known_hosts`.

**Don't have an SSH key set up?** Generate a key pair and upload the public key to the jump host. Run `ssh-keygen -t ed25519` and follow the prompts, then ask an admin to add your public key (`~/.ssh/id_ed25519.pub`) to the server.

!!! note
    `ssh_password=<your-lisc-password>` in `[noxdb-ssh]` is still accepted for file downloads (`download_files_for_project`, `export_project`), which use SFTP. It is ignored by the database tunnel.

!!! note
    Only skip this section if you run your scripts directly on `ccr-lab` (or another LiSC machine with `/lisc` mounted). Without `ssh_host`, noxdb connects straight to `mariadb.lisc` and copies files from the local `/lisc` paths, which only exist there.

!!! warning
    Don't add `local_port` to `[noxdb-ssh]` unless you open an SSH tunnel yourself (see the [quickstart](quickstart.md)). When `local_port` is set and something is listening on that port, noxdb connects to it, even if it is not the tunnel.

---

## Step 5 — Test your connection

Copy the script below into a file called `test_connection.py` and run it with `python test_connection.py`.

```python
from noxdb.connection import execute, close_pool

print("Connecting to the database...")
try:
    result = execute("SELECT 1 AS ok")
    print("Connection successful! Database responded:", result)
except Exception as e:
    print("Connection failed:", e)
    print("\nThings to check:")
    print("  1. Is ~/.my.cnf present and does it have a [noxdb] section?")
    print("  2. Are your host / user / password correct?")
    print("  3. Do you have LiSC access (on-site, VPN, or the firewall page)?")
    print("  4. If working from your own computer, does ~/.my.cnf have a [noxdb-ssh] section?")
    print("  5. Can you SSH into ccr-lab.lisc.univie.ac.at manually?")
finally:
    close_pool()
```

`execute()` opens the connection on first use, and `close_pool()` closes it again (and the SSH tunnel, if one was opened). Call `close_pool()` at the end of every script.

A successful run looks like this:

```
Connecting to the database...
Connection successful! Database responded: [{'ok': 1}]
```

If it fails, the error message and the checklist printed at the end are your first debugging steps.
