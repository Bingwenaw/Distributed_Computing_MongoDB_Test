# Standalone local/shared MongoDB lab

This folder is a complete, independent application. Copy this folder to each friend. It imports no application code from the parent project and uses its own dependencies, Docker projects, ports, database volumes, and logs.

**Default: Disconnected / local mode.** Connect switches to a nine-node replica set shared by three laptops on the same hotspot. Disconnect stops this laptop's shared nodes and restores its local three-node lab. Local and shared databases are separate; switching does not merge or copy their documents.

For your first session, use **Database = `lab`** and **Collection = `manual`** on all three laptops. You do not need to edit Python or Compose files to link up.

Quick links: [what to customise](#what-you-need-to-customise) · [first local launch](#first-local-launch) · [team setup](#prepare-the-three-laptops-once) · [prove-you-are-connected exercise](#first-shared-writeread-exercise) · [next session](#what-to-do-at-your-next-meeting) · [troubleshooting](#link-up-troubleshooting).

## What you need to customise

| Setting | What you and your friends should enter | Where | Must it match? |
| --- | --- | --- | --- |
| This laptop | You = **A**, first friend = **B**, second friend = **C** | Dashboard → Connection setup | **Different** on each laptop. Save the correct identity the first time. |
| Laptop A hotspot IPv4 | Your actual hotspot/Wi-Fi IP address | Connection setup on **all three** dashboards | **Same A address** everywhere. |
| Laptop B hotspot IPv4 | First friend's actual hotspot/Wi-Fi IP address | Connection setup on **all three** dashboards | **Same B address** everywhere. |
| Laptop C hotspot IPv4 | Second friend's actual hotspot/Wi-Fi IP address | Connection setup on **all three** dashboards | **Same C address** everywhere. |
| Shared team file | A generates it; B and C import that exact file | Create/download/import buttons in Connection setup | **Same file** for all three. Do not edit its contents. |
| Hostname mappings | The lines shown after saving settings, using your real IPs | Each laptop's OS hosts file | **Same mappings** on all three hosts. |
| Database | Keep **`lab`** for the first session | Client panel → Database | Match when you want to access the same documents. See database permissions below. |
| Collection | Keep **`manual`**, or agree on a name such as `team_demo` | Client panel → Collection | Match when you want to access the same documents. |
| Document `_id` / Read filter | Agree on a test ID, e.g. `team-check-001` | Write document and Read filter | Match when reading/updating the same document. |
| Read/write concern and causal consistency | Start with majority / majority / checked; vary later for experiments | Manual client panels or experiment controls | May differ between clients; these settings do not establish the network connection. |

**Leave these unchanged for this implementation:** node names (`mongo1`–`mongo9`), advertised hostnames (`mongoN.lab.test`), replica-set names, Docker project names, ports, voting settings, application username, and generated credentials. They are coordinated by the code. Changing only one file or one laptop's values can prevent the cluster from forming.

### Database name versus collection name

`lab.manual` means database **`lab`**, collection **`manual`**. All nine shared members replicate that same data. You do not need a different database for each laptop, and you do not need to create the database/collection on every member manually. A successful first insert can create an ordinary missing collection.

To use a different collection, everyone can enter, for example:

```text
Database:   lab
Collection: team_demo
```

Then insert a document. No connection configuration or replica-set changes are needed. Using `manual` on A and `team_demo` on B means they are looking at different collections, even though both are connected correctly.

**Changing the Database field alone is not enough to use a new database in shared mode.** The generated application user has `readWrite` permission on `lab`, plus monitoring permissions. For a database such as `dsa_project`, the coordinator must first use an administrative connection to grant the existing application user `readWrite` on that database. Afterward everyone can select `dsa_project` in their manual panels. That is a separate administrative change; it is not performed by Connect or the Database field. Keep `lab` access as well, because the automated experiments always use **`lab.experiments`**, independently of the manual database/collection fields.

The database named `lab`, the login user named `lab`, and the shared replica set named `rs-linked` have different purposes. Renaming a database does not rename the login or the replica set. You do not need to set any of them to your personal name or laptop name.

Also, **Client A / Client B inside each dashboard are not laptop A / B**. Each laptop has both client panels. “This laptop” under Connection setup assigns the physical laptop's role.

## Isolation from the original project

| Resource | This copy |
| --- | --- |
| Dashboard | http://127.0.0.1:8502 |
| Local Docker project / replica set | `mongo-connect-local` / `rs-local` |
| Local database ports | 28017–28019, bound to localhost |
| Shared Docker project / replica set | `mongo-connect-shared` / `rs-linked` |
| Shared database ports | 29017–29025 across the three laptops |
| Data and evidence | This copy's named Docker volumes and `results/` folder |

The original project's port 8501, ports 27017–27019, `mongo-local-lab` containers, volumes, and source files are not used. Local mode starts with its own fresh data. Run only one instance of this standalone copy per laptop; container ownership checks reject another directory using the same project name.

## First local launch

1. Install Docker Desktop (or Docker Engine + Compose on Linux) and [uv](https://docs.astral.sh/uv/getting-started/installation/). This application needs Python 3.12–3.14; uv can provision Python if necessary. Select the Docker build for your laptop's CPU architecture.
2. Add this line once to your OS hosts file, if it is not already present:

   ```text
   127.0.0.1 mongo1 mongo2 mongo3
   ```

   macOS/Linux: `/etc/hosts`. Windows: `C:\Windows\System32\drivers\etc\hosts`, edited as administrator. The application never edits system files for you.
3. On macOS, double-click **Start Lab.command**. If permissions were lost while copying, run `chmod +x *.command` inside this folder first.
4. Open **http://127.0.0.1:8502**. Expect Disconnected / Local lab, one primary, and two secondaries.

The launcher stops this copy's leftover shared containers from a previous session, then starts local mode. Database volumes remain saved. A startup failure is reported instead of silently claiming a disconnected state.

Manual launch from this folder, after starting Docker:

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
uv sync --locked
uv run --locked scripts/setup_lab.py --startup
uv run --locked streamlit run scripts/app.py
```

PowerShell equivalent:

```powershell
$env:UV_CACHE_DIR = "$PWD/.uv-cache"
uv sync --locked
uv run --locked scripts/setup_lab.py --startup
uv run --locked streamlit run scripts/app.py
```

Use `--startup` before manually launching Streamlit as well: it enforces disconnected startup even after a crash. Do not run the setup command while another instance is using the shared cluster.

## Prepare the three laptops once

All three owners must complete these steps. Joining the same hotspot does not guarantee that it allows peer-to-peer traffic; check that the laptops can reach one another. A private hotspot with non-sensitive lab data is assumed. Member/client authentication is enabled; transport TLS is not configured in this version.

Start by sending your friends the clean source folder described under [What to send to friends](#what-to-send-to-friends). Everyone completes [First local launch](#first-local-launch) on their own laptop. Keep the original project and this standalone copy distinct; use this folder's launcher and port **8502**.

1. Assign identities **A**, **B**, and **C**. A coordinates the first cluster initialization. Keep each folder's identity fixed once saved.
2. Join the same hotspot through the laptop's Wi-Fi settings. The dashboard's Connect button connects MongoDB; it does not join a Wi-Fi network for you. Find each laptop's hotspot IPv4 address using the instructions below. Enter **all three addresses** under **Connection setup** in each dashboard, not just your own address. Use the same address table everywhere.
3. On A, click **Create team file on A**, then **Download private team file**. Send that file privately to B and C.
4. B and C select their own identity and import the same `team.json`. They must not generate separate team files.
5. Click **Save connection settings** on each laptop. The shared key/application credentials are stored privately under `secrets/`; A's bootstrap administrator password is generated separately and stays on A.
6. Copy the displayed hostname mappings into every laptop's hosts file. Example only—replace the IPs:

   ```text
   127.0.0.1 mongo1 mongo2 mongo3
   192.168.43.10 mongo1.lab.test mongo2.lab.test mongo3.lab.test
   192.168.43.11 mongo4.lab.test mongo5.lab.test mongo6.lab.test
   192.168.43.12 mongo7.lab.test mongo8.lab.test mongo9.lab.test
   ```

7. Permit inbound connections from the participating laptops on the shared database ports in the host firewall. A publishes 29017–29019; B publishes 29020–29022; C publishes 29023–29025. Do not expose Docker's control socket. The application publishes only on the configured hotspot address.
8. Keep the hotspot addresses stable for the session and disable laptop sleep. Download the MongoDB image/dependencies beforehand if hotspot internet is limited. Check available RAM for three MongoDB containers per laptop.

The generated `compose.linked.json` contains only the three services owned by this laptop. Local peers resolve through Docker aliases; remote peers resolve through per-container host mappings. No overlay network or remote-control server is needed.

### Find the correct IP address

Check the address **after joining the hotspot**. Use the IPv4 address of the connected Wi-Fi adapter, not `127.0.0.1`, the hotspot's gateway/router address, a public internet IP, or a Docker/VPN adapter address.

| OS | How to find it |
| --- | --- |
| macOS | Run `networksetup -listallhardwareports` in Terminal. Find the Wi-Fi device, such as `en0`. Then run `ipconfig getifaddr en0`, substituting the actual device name if different. |
| Windows | Run `ipconfig` in Command Prompt or PowerShell. Read **IPv4 Address** under the connected Wi-Fi adapter. |
| Linux | Run `ip -4 addr` and read the `inet` address of the connected Wi-Fi interface. Enter only the IP, without the `/24` or other prefix suffix. |

Write down your team's values before entering them:

| Owner | Identity | Actual hotspot IPv4 | Shared ports to allow |
| --- | --- | --- | --- |
| You | A | Fill in your address | TCP 29017–29019 |
| Friend 1 | B | Fill in their address | TCP 29020–29022 |
| Friend 2 | C | Fill in their address | TCP 29023–29025 |

For example, if you recorded A=`192.168.43.10`, B=`192.168.43.11`, and C=`192.168.43.12`, **every dashboard gets those same three values**. Only the **This laptop** selection differs.

### Edit the hosts file on each laptop

- **macOS/Linux:** open Terminal and run `sudo nano /etc/hosts`. Add the dashboard's displayed mappings. In nano, save with Ctrl+O, press Enter, then exit with Ctrl+X.
- **Windows:** open Notepad as administrator, open `C:\Windows\System32\drivers\etc\hosts` (select All Files if necessary), add the displayed mappings, and save without adding a `.txt` extension.

Keep unrelated entries intact. If a `mongoN.lab.test` entry already exists with an old IP, update it rather than adding a conflicting duplicate. The local `127.0.0.1 mongo1 mongo2 mongo3` line and shared `.lab.test` lines serve different modes; keep both.

At this point, A should have generated/downloaded the team file, B/C should have imported it, and all three should have saved settings and updated their hosts files. You are ready to click Connect together.

## Connect, disconnect, and reconnect

**Each laptop clicks Connect within the same two-minute window.** The dashboard shows progress while local containers are paused, its three shared containers start, and all nine endpoints are checked. A initializes a new set once and creates the application user; subsequent connections reuse the existing configuration and data. Containers also check peer access from inside Docker.

Connected status requires a matching authenticated configuration and one primary plus eight secondaries. The dashboard then displays all nine members and allows reads targeted to any of them. Writes still go to the primary. The application account has read/write access to `lab`; entering another database name does not grant access to it.

| Laptop | Members | Votes |
| --- | --- | --- |
| A | mongo1, mongo2, mongo3 | 3 |
| B | mongo4, mongo5, mongo6 | 2; mongo6 does not vote |
| C | mongo7, mongo8, mongo9 | 2; mongo9 does not vote |

The cluster has seven voters, with a majority of four. Non-voters still replicate and can serve reads. Any single laptop's loss leaves enough voters for a new primary, assuming the others remain healthy and connected. Losing two laptops leaves no majority. See [MongoDB voting members](https://www.mongodb.com/docs/v7.0/core/replica-set-elections/) and [write concern](https://www.mongodb.com/docs/v7.0/reference/write-concern/).

**Disconnect** finishes the mode switch only when active dashboard jobs are idle. If an experiment is running, use **Stop experiments**, wait for recovery, then Disconnect. It closes shared client sessions, stops this laptop's three shared containers, preserves their volumes, and restores the local lab with new client sessions. Other laptops' containers keep running.

**Cancel connection** requests cancellation of pending setup. The current bounded Docker/MongoDB step may need to finish before local recovery starts. Setup time also includes image download, election, and initial replication; it is not a fixed two-minute total.

A failed Connect restores local mode where possible and displays the reason. If recovery fails, controls remain blocked and **Return to local mode** retries recovery. A network failure after connection shows Checking / degraded; it does not silently move writes into the local database. All browser tabs for one dashboard share the selected mode and its clients.

## First shared write/read exercise

Do this before changing concerns or injecting faults. All three dashboards should show **Connected · Shared cluster**, with one PRIMARY and eight SECONDARY members. A document inserted while the dashboard said Disconnected belongs to that laptop's separate local lab and will not appear in the shared database.

1. On **all three laptops**, use either manual client panel and select Database **`lab`**, Collection **`manual`**, Read concern **`majority`**, Write concern **`majority`**, and Read from **`primary`**. Leave Causal consistency checked and Upsert unchecked.
2. On **your laptop A**, choose `insert_one`, enter the following in **Write document / update**, and click **Write** once:

   ```json
   {"_id":"team-check-001","message":"Hello from laptop A","version":1}
   ```

3. On **friends' laptops B and C**, enter this in **Read filter** and click **Read**:

   ```json
   {"_id":"team-check-001"}
   ```

   Both should see the same document. They do not need to insert it themselves. If it has not appeared yet, wait briefly and read again; separate clients do not inherit A's causal session history.
4. On **laptop B**, keep that Read filter, choose `update_one`, enter the following, and click **Write**:

   ```json
   {"$set":{"message":"Updated by laptop B","version":2}}
   ```

5. On **A and C**, read the same ID again and verify `version: 2`. This confirms that both friends can access the shared document and that B can write to it.
6. To inspect replication, change **Read from** to individual nodes (`mongo1` through `mongo9`) and read again. Allow catch-up time; a secondary can lag, and majority read concern alone is not a promise of the newest value.

Use a fresh ID such as `team-check-002` for a later repeat of the insert. A duplicate-key error for `team-check-001` means the document already exists; use `update_one` or a new ID instead of inserting the same ID again.

Once this works, vary read/write concerns in the manual panels or use the experiment section. Changing a concern does not require disconnecting and reconnecting the laptops.

## What to do at your next meeting

1. Everyone joins the same hotspot and starts Docker and this folder's dashboard.
2. Check the three IP addresses again. If any changed, save the updated A/B/C address table on **all three dashboards** while disconnected and update all three hosts files.
3. Reuse the existing team file, saved identity, and credentials. Do not generate a new team for every meeting.
4. All three click Connect and wait for the shared cluster to be ready. Previously written shared documents remain in the saved volumes.
5. When finished, stop experiments and wait for recovery, then Disconnect and/or use this folder's Stop Lab.command. Each owner shuts down their own laptop's lab.

You do not need to repeat dependency installation, identity assignment, or team-file import at every meeting if those files and the environment are still present.

## Link-up troubleshooting

| What you see | What to check |
| --- | --- |
| Connect says to save settings first | Select the correct identity, enter all three IPs, create/import the shared file, and click Save connection settings. |
| “Hosts entry needed” | Update the OS hosts file on that laptop to the displayed IP/hostname mapping. Saving dashboard settings does not edit the OS file. |
| Waiting for all nine endpoints / timeout | Both friends must click Connect too. Check their IPs, Docker, firewall ports, and whether the hotspot permits devices to communicate. Your button does not start their computers or containers. |
| Authentication or matching-team failure | All three must use the exact file generated by A. Preserve the existing secrets files for an initialized cluster. |
| “Laptop identity is fixed” | A configured copy cannot be reassigned using the dropdown. Follow the fresh-copy/old-deployment cleanup guidance under Shutdown and recovery. Do not casually delete credentials from an existing setup. |
| “Not authorized” after entering another database | Return to `lab`, or have the coordinator grant access to the intended database first. |
| Read returns `[]` | Check Connected status, matching Database and Collection fields, the exact `_id` filter, and whether the write completed. Check that the document was written in shared mode. |
| One laptop shows only three nodes | It is still in local mode or its connection failed. Check its connection message before writing more data. |
| Connect/Disconnect is disabled | Finish active jobs. For experiments, click Stop experiments and wait for recovery. |
| Nine nodes were connected, then some became unreachable | Check Wi-Fi, sleep/power state, and Docker on the affected laptop. The app stays in shared mode rather than switching writes to local data. |

## Reads, writes, and experiments

The two manual client panels retain the original behavior:

- `insert_one`: add a new document. Reusing its `_id` gives a duplicate-key error.
- `update_one`: use the Read filter to match a document, and an update such as `{"$set":{"version":2}}`.
- `replace_one`: use the same filter and supply complete replacement contents. Omitted fields are removed except `_id`.
- Clicking Read before Write is optional. The Read filter also selects update/replace targets. Use an exact `_id` when targeting a particular document.
- Upsert creates a document if update/replace finds no match. It does not change insert behavior.

Under **Repeatable experiments & predictions**, the experiment-specific read/write concerns and causal checkbox govern automated trials. The manual panel's values do not govern the experiment runner. “Compare all 8” tests local/majority reads × 1/majority writes × causal on/off. Each trial creates new sessions and a unique document in `lab.experiments`.

Local mode retains all five original fault scenarios. Shared mode supports automated **Normal** trials; node faults are controlled manually by their owners. Only one laptop should run a formal experiment at a time. Shared automated fault orchestration is not implemented.

“No violation observed” describes the sampled history; it is not proof of a guarantee. Errors and unavailable operations can produce “inconclusive.” A timeout may leave a write outcome unknown. Predictions follow [MongoDB's causal concern table](https://www.mongodb.com/docs/manual/core/causal-consistency-read-write-concerns/).

The fault selector controls only this laptop's members. **Restore this laptop** restores those members, including their hostname aliases. **Clear MongoDB data** remains available only for this copy's local lab and requires confirmation; shared reset is blocked in both UI and backend.

## Shutdown and recovery

On macOS, finish experiments and double-click this folder's **Stop Lab.command**. It targets Streamlit processes whose working directory is this folder and stops both of this copy's Docker projects. It preserves database volumes and logs. Docker Desktop and unrelated projects stay running.

On other systems, stop experiments, close the Streamlit terminal with Ctrl+C, then run:

```sh
uv run --locked scripts/shutdown.py
```

Local recovery: `uv run --locked faults/control.py restore`.

Shared recovery for this owner's three nodes: `uv run --locked faults/control.py restore --linked`.

If IP addresses change, Disconnect on each laptop, save the updated address table, update OS hosts entries, and reconnect together. If a connection fails, check hostname resolution, hotspot peer isolation, firewall rules, whether both friends clicked Connect, and matching team files. An unexpected existing replica configuration is rejected without forcing reconfiguration or deleting data.

Do not remove `secrets/` from an already initialized installation; it contains the credentials required to reconnect. To form a different team or change a folder's laptop identity, use a fresh copy and separately plan cleanup of the old deployment. Do not run two copies using the same Docker project names simultaneously on one laptop.

## What to send to friends

Copy this entire source folder **excluding** `.venv/`, `.uv-cache/`, `__pycache__/`, `secrets/`, `results/`, temporary test directories, and generated `compose.linked.json`. Retain both launchers, `scripts/`, `faults/`, `.streamlit/`, `compose.local.yml`, `pyproject.toml`, and `uv.lock`. Each friend creates their own environment and volumes. Send the team file separately after assigning identities.

No Python source or configuration is loaded from the parent folder. Your existing original database is not copied into this application.

## Verification

Run inside this folder:

```sh
uv run --locked test.py --ui
uv run --locked test_linked.py
```

An additional authentication-script check runs when Node.js is available; Node.js is not needed to run the application.

These checks use temporary files inside this folder, mock Docker mutations and linked startup, and parse generated Compose configurations without starting containers. They test the local UI, nine-node shared UI, ownership limits, cancelled/failed connections, restoration to local mode, and independent project resources.

Optional **local live checks**: `uv run --locked test.py --integration` and `uv run --locked test.py --faults`. These write lab documents; the latter injects real local faults. Keep all dashboard experiments idle while running them.

The multi-laptop acceptance test must still be performed on the real hotspot: connect all owners, write on A, observe the document on B/C and all nine tagged read targets, disconnect/reconnect each owner, and confirm that local/shared data remain separate. Independent clients do not automatically share causal session history; allow replication to catch up when observing another laptop's write.

Implementation status and differences from the original design are recorded at the top of [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
