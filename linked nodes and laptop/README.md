# Standalone MongoDB lab — connect without team files

This version uses **no MongoDB passwords or team files**. Choose your laptop letter, enter the three Wi-Fi IP addresses, save, and connect together. The app assigns the nine nodes automatically.

**Use a trusted private hotspot and test data only. Anyone who can reach the database ports can read or change data.** Keep the database ports off the public internet.

For click-by-click instructions for **A (Mac), B (Mac), and C (Windows)**, open [setup_connection.md](setup_connection.md).

## If you used the older version

1. Everyone stops their dashboard and lab using the old copy's Stop Lab.command (Mac), or Ctrl+C then `uv run --locked scripts/shutdown.py` (Windows).
2. Everyone updates the app files in their existing `linked nodes and laptop` folder. Keep the existing `secrets/laptop.json`, which stores the laptop letter and IP addresses. Keep all Docker volumes.
3. Restart everyone's dashboard. The team-file create/import/download controls should be gone.
4. Save the correct A/B/C letter and all three IPs, check hosts-file mappings, and click Connect together.

All three laptops must run this new version. Mixing it with the password-based version will fail. Old `team.json`, `member.key`, and `admin.json` files are left untouched and are no longer read or mounted. There is no need to delete them. Existing matching replica-set configurations and database volumes are reused. Old team tags are ignored; node names, hosts, votes, and ownership are still checked. The app does not force a foreign configuration or reset shared data.

Run only one copy per laptop. On A, the currently used Git checkout is under **Documents/Git Hub/Distributed Computering/linked nodes and laptop**. Always launch that same copy; another folder may have different saved IP settings.

## What to enter

| Dashboard field | Value |
| --- | --- |
| This laptop | A on your Mac, B on your friend's Mac, C on your Windows friend's laptop |
| Laptop A/B/C hotspot IPv4 | Each person's actual Wi-Fi IP; enter the same full table on every laptop |
| Database | Start with `lab` |
| Collection | Start with `manual` |

`lab.manual` is the database and collection shared by all nine nodes. You can choose another ordinary database or collection without an administrator grant now; everyone must use the same names to see the same documents. Automated experiments always use `lab.experiments`, independently of the manual fields.

The two **Client A / Client B** panels within each dashboard are independent database clients, not physical laptop identities.

## First launch

Install Docker Desktop and [uv](https://docs.astral.sh/uv/getting-started/installation/). This app requires Python 3.12–3.14; uv can install a compatible Python.

Each laptop needs this line in its OS hosts file:

```text
127.0.0.1 mongo1 mongo2 mongo3
```

macOS: `/etc/hosts`. Windows: `C:\Windows\System32\drivers\etc\hosts`, edited using Notepad as administrator. See the setup guide for exactly how to open and save these files.

On Mac, start Docker Desktop and double-click **Start Lab.command**. If execution permissions were lost, run `chmod +x *.command` in this folder. Open **http://127.0.0.1:8502**.

On Windows, use **PowerShell in this folder** and Docker Desktop with Linux containers. Run one line at a time:

```powershell
$env:UV_CACHE_DIR = "$PWD/.uv-cache"
uv sync --locked
uv run --locked scripts/setup_lab.py --startup
uv run --locked streamlit run scripts/app.py
```

Manual Mac launch uses the same uv commands, with `export UV_CACHE_DIR="$PWD/.uv-cache"` for the first line. Use `--startup` before launching Streamlit so leftover shared containers are stopped and the app starts disconnected. Do not run it during an active experiment.

## Connect and disconnect

Everyone joins the same hotspot, enters the three IPs in Connection setup, and clicks Save connection settings. Copy the displayed address lines into each laptop's hosts file, preserving unrelated entries and replacing any old mappings. Every laptop needs all nine shared hostnames.

Allow incoming database connections on the owner's ports:

| Owner | Shared members | TCP ports | Votes |
| --- | --- | --- | --- |
| A | mongo1–mongo3 | 29017–29019 | 3 |
| B | mongo4–mongo6 | 29020–29022 | 2 |
| C | mongo7–mongo9 | 29023–29025 | 2 |

Everyone clicks **Connect** within the same two-minute window. Each button starts only its owner's three nodes. A initializes the new set; all dashboards wait for **one primary and eight secondaries**. Seven members vote; mongo6 and mongo9 still store data but do not vote. The voting majority is four.

Some hotspots block communication between devices. Internet access alone does not prove your laptops can reach each other. Keep all laptops awake, and recheck IPs if a connection fails or the hotspot changes.

**Disconnect** closes shared sessions, stops this laptop's shared containers, preserves volumes, and restores its separate local three-node lab. Local and shared documents are never merged or copied. Failed setup restores local mode where possible; **Return to local mode** retries failed recovery. Network loss after a successful connection shows degraded status and does not redirect writes to local data.

Stop active experiments and wait for recovery before switching. **Cancel connection** takes effect after the current bounded step completes. Image download, elections, and replication may take longer than the initial two-minute coordination window.

## Reads, writes, and experiments

- `insert_one` creates a document; reusing an existing `_id` causes a duplicate-key error.
- `update_one` uses the **Read filter** to select a document and an update such as `{"$set":{"version":2}}`.
- `replace_one` uses the same filter and replaces the document's contents. Omitted fields are removed except `_id`.
- Clicking Read before Write is optional. The filter must match the intended document; an exact `_id` is simplest.
- Upsert creates a document if an update/replacement finds no match.

Use `lab.manual`, majority read/write concerns, and primary reads for your first shared test. Insert once on A, then have B and C read the same ID. The setup guide supplies copy-and-paste examples.

Manual concern settings and the **Repeatable experiments & predictions** settings are independent. Shared mode supports automated **Normal** trials. Only one laptop should run a formal experiment at a time. Shared faults are controlled manually by each owner; remote fault orchestration is not implemented. Local mode retains all five scenarios.

Secondary reads may lag. Independent laptops do not automatically share causal session history. “No violation observed” describes the sampled history, not proof of a guarantee. Predictions follow [MongoDB's causal concern table](https://www.mongodb.com/docs/manual/core/causal-consistency-read-write-concerns/).

## Shutdown and isolation

Mac: double-click this folder's **Stop Lab.command**. Windows: press Ctrl+C in the dashboard PowerShell, then run `uv run --locked scripts/shutdown.py`.

The stop command preserves volumes and results. It targets only this copy's Docker projects and, on Mac, dashboard processes running from this exact folder. Docker Desktop and other projects remain running.

| Resource | This standalone copy |
| --- | --- |
| Dashboard | localhost:8502 |
| Local project / replica set | `mongo-connect-local` / `rs-local` |
| Local ports | 28017–28019, localhost only |
| Shared project / replica set | `mongo-connect-shared` / `rs-linked` |
| Shared ports | 29017–29025, bound to the saved Wi-Fi IP on each owner |
| Settings | `secrets/laptop.json` (letter and IPs only) |
| Generated Compose | `compose.linked.json` |

No application code is imported from the parent folder. The original project's ports 8501 and 27017–27019 and its database volumes are separate.

Local recovery: `uv run --locked faults/control.py restore`. Shared owner recovery: `uv run --locked faults/control.py restore --linked`. Shared data reset remains blocked. Local Clear MongoDB data still requires confirmation.

## Files for friends and checks

Send the source files, including hidden `.streamlit/`, but exclude `.venv/`, `.uv-cache/`, `__pycache__/`, `secrets/`, `results/`, and generated `compose.linked.json`. Each person keeps their own existing settings when updating. There is no separate team file to send.

Run inside this folder:

```sh
uv run --locked test.py --ui
uv run --locked test_linked.py
```

These check the manual UI, setup without credentials, shared inventory and controls, bootstrap error reporting, preservation of matching old configurations, and rollback. They do not launch real lab containers. Real local integration checks are available through `test.py --integration`; `--faults` injects local faults.

Optional isolated Docker check: `uv run --locked test_linked.py --docker`. It creates three temporary members on random localhost ports, checks password-free initialization and replicated writes, restarts them to verify persistence, then removes only its temporary containers and volumes. It does not use your saved laptop settings or lab databases.

The actual three-laptop hotspot connection remains the final hardware acceptance check. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) retains the older design as history; this README describes current behavior.
