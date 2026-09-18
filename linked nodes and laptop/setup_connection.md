# Connect A (Mac), B (Mac), and C (Windows)

**You no longer need team.json, database passwords, or matching team codes.** The app connects using your laptop letter and the three Wi-Fi addresses.

Use a private hotspot you trust and test data only. Anyone who can reach the database ports can read or change the data.

## If you already tried the older version

Everyone must get the updated app files first. The old and new versions cannot connect to each other.

1. Stop the old dashboard: A/B use **Stop Lab.command**; C presses **Ctrl+C** in PowerShell and runs `uv run --locked scripts/shutdown.py`.
2. Update the app in the same folder you were using. Keep your saved settings and data; there is no need to delete anything.
3. Restart it. **The Create/Import team file buttons should now be gone.**

A: use the copy at **Documents → Git Hub → Distributed Computering → linked nodes and laptop**. Keep using that same folder each time.

## Everyone: join the hotspot and collect three numbers

Use each laptop's usual Wi-Fi menu to join the same hotspot. Ask its owner for the name and Wi-Fi password. Keep the laptops awake and plugged in.

You need each laptop's **IPv4 address**. This is its address on this Wi-Fi. You will copy the numbers; you do not need to choose or change them.

| Person | Where to find their address |
| --- | --- |
| A and B — Mac | Apple menu → System Settings → Network → Wi-Fi → Details → TCP/IP. Copy **IP address / IPv4 address**, not Router. |
| C — Windows | Start → Settings → Network & internet → Properties for the connected Wi-Fi. Copy **IPv4 address**, not DNS servers. |

See [Apple's settings guide](https://support.apple.com/en-ph/guide/mac-help/mh14129/mac) or [Microsoft's settings guide](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/essential-network-settings-and-tasks-in-windows) if the layout differs.

Share the three numbers in a message or note:

```text
A's IP:
B's IP:
C's IP:
```

Your last recorded addresses were A=`10.255.178.92`, B=`10.255.178.226`, C=`10.255.178.91`. **Check them again: Wi-Fi may assign different addresses later.**

## Person A — Mac

1. Open **Docker Desktop** and wait for it to finish starting.
2. Open your **linked nodes and laptop** folder under Documents/Git Hub/Distributed Computering.
3. Double-click **Start Lab.command**. Keep its Terminal window open.
4. Open Safari or Chrome and visit **http://127.0.0.1:8502**. You should see **Disconnected · Local lab**.
5. Open **Connection setup** and fill these fields:

   | Field | What to put there |
   | --- | --- |
   | This laptop | **A** |
   | Laptop A hotspot IPv4 | A's number from your shared note |
   | Laptop B hotspot IPv4 | B's number from your shared note |
   | Laptop C hotspot IPv4 | C's number from your shared note |

6. Click **Save connection settings**. You should see **Settings saved** and a box of address lines.
7. Copy the **whole address box**. Follow **Add the address lines on Mac** below. If the exact same lines are already in your hosts file, you can skip adding them again.
8. Follow **Mac connection permission** below if you have not already done it.
9. Wait for B and C. Do not click Connect early.

You do not create, import, or send a team file.

## Person B — Mac

1. Get the updated app files from A in your existing **linked nodes and laptop** folder. For a new installation, unzip the source into your own folder first.
2. Open **Docker Desktop** and wait for it to finish starting.
3. Double-click **Start Lab.command** in your app folder. Keep its Terminal window open.
4. On **your own Mac**, open **http://127.0.0.1:8502** in a browser.
5. Open **Connection setup** and fill these fields:

   | Field | What to put there |
   | --- | --- |
   | This laptop | **B** — change it from A if this is your first setup |
   | Laptop A hotspot IPv4 | A's number from your shared note |
   | Laptop B hotspot IPv4 | B's number from your shared note |
   | Laptop C hotspot IPv4 | C's number from your shared note |

6. Click **Save connection settings**.
7. Copy the **whole address box** and follow **Add the address lines on Mac**. Keep existing correct lines once; replace old addresses if they changed.
8. Follow **Mac connection permission** if needed.
9. Wait for A and C. There is no team file to import.

## Person C — Windows

1. Get the updated app files in your existing folder. For a new installation, right-click the ZIP → **Extract All** and open the extracted folder.
2. Start **Docker Desktop**. It must use Linux containers: if its menu offers **Switch to Linux containers**, choose it. Leave it alone if it offers **Switch to Windows containers**.
3. In **File Explorer**, open the app folder where you can see **pyproject.toml** and **scripts**.
4. Click File Explorer's **address bar** at the top, type `powershell`, and press Enter. This opens PowerShell in the correct folder.
5. Paste these lines **one at a time**, pressing Enter and waiting after each. Stop if a line reports an error:

   ```powershell
   $env:UV_CACHE_DIR = "$PWD/.uv-cache"
   uv sync --locked
   uv run --locked scripts/setup_lab.py --startup
   uv run --locked streamlit run scripts/app.py
   ```

6. Keep PowerShell open. Open Edge or Chrome and visit **http://127.0.0.1:8502**.
7. Open **Connection setup** and fill these fields:

   | Field | What to put there |
   | --- | --- |
   | This laptop | **C** — change it from A if this is your first setup |
   | Laptop A hotspot IPv4 | A's number from your shared note |
   | Laptop B hotspot IPv4 | B's number from your shared note |
   | Laptop C hotspot IPv4 | C's number from your shared note |

8. Click **Save connection settings**. Copy the **whole address box** that appears.
9. Follow **Add the address lines on Windows** and **Windows connection permission** below.
10. Wait for A and B. There is no team file to import.

## Add the address lines on Mac — A and B

1. Open a **new** Terminal window: Command+Space → type **Terminal** → Enter. Keep the dashboard's Terminal running.
2. Paste this and press Enter:

   ```sh
   sudo nano /etc/hosts
   ```

3. Enter your **Mac login password**, then press Enter. The password stays invisible while typing.
4. Move to the bottom using the arrow keys. **Keep existing unrelated text.** Paste the address box you copied from the dashboard using **Command+V**.
5. Keep identical lines only once. Replace old `.lab.test` lines if their IPs changed.
6. Save: **Control+O**, then **Enter**. Exit: **Control+X**. Use Control, not Command, for those save/exit keys.

Both Macs need every address line, not just their own nodes. The first line, `127.0.0.1 mongo1 mongo2 mongo3`, is also the same for everyone; it supports local mode.

## Add the address lines on Windows — C

1. Open Start, search **Notepad**, right-click it, and choose **Run as administrator**. Click Yes if asked.
2. Click **File → Open**. Paste this into the **File name** box and click Open:

   ```text
   C:\Windows\System32\drivers\etc\hosts
   ```

3. Preserve unrelated text and paste the whole address box from your dashboard at the bottom.
4. Keep identical lines once and replace old `.lab.test` lines if their IPs changed.
5. Press **Ctrl+S**. The filename must stay **hosts**, without `.txt`. If browsing for the file, select **All Files** in the Open window.

## Mac connection permission — A and B

Open **System Settings → Network → Firewall → Options** if your firewall is on. Leave **Block all incoming connections** unselected and allow incoming connections for **Docker / com.docker.backend** if listed. Click **Allow** if macOS shows a Docker connection prompt. Do not switch off the firewall. [Apple's firewall guide](https://support.apple.com/en-au/guide/mac-help/mh11783/mac)

If you have another security app that filters ports, A needs incoming TCP **29017–29019** and B needs **29020–29022** from the team laptops.

## Windows connection permission — C

If you already created the rule and A/B's IPs have not changed, skip this section.

1. Open Start, search **PowerShell**, right-click it, and choose **Run as administrator**. Keep the dashboard's other window running.
2. Paste the first line below and press Enter. When asked, type **A's actual IP from your shared note**, then Enter.
3. Do the same with the second line, entering **B's actual IP**.
4. Paste the third line exactly as written and press Enter. Do not change its port numbers:

   ```powershell
   $labPeerA = Read-Host "Type A's Wi-Fi IP, then press Enter"
   $labPeerB = Read-Host "Type B's Wi-Fi IP, then press Enter"
   New-NetFirewallRule -Name "LinkedMongoLab-C" -DisplayName "Linked Mongo lab - C" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 29023-29025 -RemoteAddress $labPeerA,$labPeerB -Profile Any
   ```

If Windows says that rule already exists, use this instead of the third line:

```powershell
Get-NetFirewallRule -Name "LinkedMongoLab-C" | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $labPeerA,$labPeerB
```

This allows the two specified laptops to connect to C's database ports. It leaves Windows Firewall on. [Microsoft's command reference](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule)

## Connect together

1. Check that everyone has the updated app: **no team-file buttons** should be present.
2. Check everyone saved the same three IPs and added the address lines.
3. A says **“Click Connect now.”** All three click Connect on their own dashboards within two minutes of each other.
4. Do not wait for A to finish before B and C click. Keep Docker, the dashboards, and Wi-Fi running.
5. Success means **Connected · Shared cluster** on each dashboard, with **one PRIMARY and eight SECONDARY** nodes. Setup may take a few minutes.

## Check that sharing works

Everyone uses the manual **Client A** panel on their own dashboard. That panel name is unrelated to your physical laptop's letter.

Set **Database = lab**, **Collection = manual**, **Read concern = majority**, **Write concern = majority**, **Read from = primary**. Tick Causal consistency; leave Upsert unticked.

**A:** choose `insert_one`, put this in **Write document / update**, and click **Write** once:

```json
{"_id":"hello-team-001","message":"Hello friends"}
```

**B and C:** put this in **Read filter**, then click **Read**:

```json
{"_id":"hello-team-001"}
```

Both should see **Hello friends**. For another test, use a new ID such as `hello-team-002` in both places. A duplicate-key error means you already inserted that ID.

You can use another ordinary database or collection name, but everyone must enter the same names to see the same data. No administrator permission grant is needed in this version.

## Finish for the day

Stop experiments and wait for recovery first.

- **A/B:** double-click **Stop Lab.command** in the folder you used to start the app.
- **C:** press **Ctrl+C** in the dashboard PowerShell, then run `uv run --locked scripts/shutdown.py`.

Saved data stays. Closing the browser alone does not stop the databases. Use **Disconnect** if you want to continue with your own local lab instead. Local and shared data are separate.

Next time, recheck IPs, update everyone's saved address table and hosts lines if they changed, and connect together. C also updates the firewall rule if A/B's addresses changed.

## First-time installation only

Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) or [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/). On Mac, Apple menu → About This Mac tells you whether to choose Apple silicon or Intel. On Windows, follow Docker's WSL 2 prompts and restart if asked.

Install uv by opening Terminal (Mac) or PowerShell (Windows) and running the appropriate command from [uv's installer guide](https://docs.astral.sh/uv/getting-started/installation/):

Mac:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen the terminal after installing. Before your first dashboard launch, add this line to your hosts file using the Mac/Windows editing steps above:

```text
127.0.0.1 mongo1 mongo2 mongo3
```

If Mac says Start Lab.command lacks permission, open Terminal, type `cd `, drag the app folder into Terminal, press Enter, and run `chmod +x "Start Lab.command" "Stop Lab.command"`.

## If it still fails

Send A the **person letter, step number, and full error message**. There are no team files to compare now.

- Still seeing team-file buttons or “Waiting for authentication”? That laptop has the old app or did not fully restart it.
- “Hosts entry needed”? Save the dashboard's address lines in that laptop's hosts file.
- Waiting for all laptops? Check everyone clicked Connect, Docker is running, and the IPs/firewall rules are correct. Some hotspots block devices from contacting one another; another hotspot/router may be needed.
- An unexpected existing replica set? Do not delete data. Ask for help checking its saved configuration.
- Can't see a document? Check Connected status, matching database/collection/ID, and that the Write succeeded. A disconnected local write does not appear in shared mode.

More details: [README.md](README.md).
