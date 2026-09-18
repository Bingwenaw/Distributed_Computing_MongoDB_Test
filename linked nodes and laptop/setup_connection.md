# Link your three laptops — step by step

**You are Person A (Mac). Your friends are Person B (Mac) and Person C (Windows).**

When you finish, all three dashboards will use the same shared database. Each laptop will run three nodes, giving you nine in total. The app assigns the nodes for you.

Read **Before you start**, follow **your own person's steps**, then do **Connect together** as a group. Do not press Connect early.

## Before you start

1. Turn on the hotspot you plan to use.
2. All three people connect to that same hotspot using their laptop's normal Wi-Fi menu. Ask the hotspot owner for its name and password.
3. Keep the laptops open, awake, and plugged in.
4. Use only the project folder called **linked nodes and laptop** for these steps.

You will collect three numbers called **IP addresses**. An IP address is how the other laptops find your laptop on this Wi-Fi. It looks something like `192.168.43.10`. **Do not copy that example: the steps below show where to find your own.**

Put your actual addresses in a shared message or note:

```text
Person A's IP: 
Person B's IP: 
Person C's IP: 
```

You do not need to choose node names, passwords for MongoDB, or a database name. For your first test, use database **lab** and collection **manual** exactly as written.

## Person A — Mac

### A1. Send the app to your friends

You only need to do this if B and C do not already have the latest copy from you.

1. Open **Finder** and locate **linked nodes and laptop**.
2. Press **Command + Space**, type **Terminal**, and press Enter.
3. Type `cd` followed by **one space**. Do not press Enter yet.
4. Drag the **linked nodes and laptop folder** from Finder into Terminal. Its location will appear after `cd`.
5. Press **Enter**.
6. Copy this whole line, paste it into Terminal, and press Enter. You do not need to change anything in it:

   ```sh
   zip -r linked-lab-source.zip scripts faults .streamlit compose.local.yml pyproject.toml uv.lock "Start Lab.command" "Stop Lab.command" README.md setup_connection.md IMPLEMENTATION_PLAN.md test.py test_linked.py -x '*/__pycache__/*'
   ```

7. Return to the folder in Finder. You should now see **linked-lab-source.zip**. Send that file to B and C.

This packages the app without copying your private connection settings. You will send a second, small connection file later in A6.

### A2. Open your dashboard

Your dashboard has already worked on this Mac, so you normally do not need to reinstall anything.

1. Open **Docker Desktop** from Applications and wait for it to finish starting.
2. Open your **linked nodes and laptop** folder.
3. Double-click **Start Lab.command**. Leave the Terminal window it opens running.
4. Open Safari or Chrome. Paste **http://127.0.0.1:8502** into the address bar and press Enter.

**You should see:** your dashboard saying **Disconnected · Local lab**. Disconnected is correct at this stage.

If this Mac has never run the app, complete **First-time Mac installation** near the end of this guide first. If startup asks for a hosts entry, use **First-time Mac local entry** there too.

### A3. Find your IP address

1. Click the **Apple menu** at the top left of the screen.
2. Open **System Settings → Network → Wi-Fi → Details → TCP/IP**. Choose the Wi-Fi connection for your hotspot.
3. Find **IP address** or **IPv4 address**. Copy that number into your shared note as **Person A's IP**.
4. Do not change any settings on that screen. Do not copy the number beside **Router**.

These labels come from [Apple's network settings guide](https://support.apple.com/en-ph/guide/mac-help/mh14129/mac).

### A4. Wait for both friends' IP addresses

B will find theirs in B3. C will find theirs in C4.

**Do not guess these numbers.** Continue when the shared note has all three addresses.

### A5. Enter the addresses in your dashboard

Click **Connection setup · complete once on each laptop** to open that section.

| Field on your screen | What you put there |
| --- | --- |
| This laptop | Select **A** |
| Laptop A hotspot IPv4 | Copy **Person A's IP** from your shared note |
| Laptop B hotspot IPv4 | Copy **Person B's IP** from your shared note |
| Laptop C hotspot IPv4 | Copy **Person C's IP** from your shared note |

Enter only the numbers and dots. Do not add `http://`, names, or a port number.

### A6. Create and send the connection file

1. Click **Create team file on A**.
2. Click **Download private team file**.
3. Find **team.json** in your Mac's **Downloads** folder, or the download location shown by your browser.
4. Send that file privately to B and C. They need the **file itself**, not a screenshot of its contents.
5. Click **Save connection settings** in your dashboard.

**You should see:** **Settings saved**, followed by a box containing address lines.

If you created this team's file before, reuse **Download private team file**. There is no need to create another one. Do not open or edit the file.

### A7. Copy the address lines into your Mac

These lines go in a small system file named **hosts**. Think of it as an address book for the laptops.

1. In the dashboard, copy the **whole address box** shown after saving. It starts with `127.0.0.1 mongo1 mongo2 mongo3` and includes three more lines.
2. Open a **new Terminal window**. Keep the dashboard's running Terminal open.
3. Paste this command and press Enter:

   ```sh
   sudo nano /etc/hosts
   ```

4. Type your **Mac login password**, then press Enter. Nothing appears as you type the password; that is normal.
5. Use the arrow keys to move to the bottom of the file. **Keep the existing text.**
6. Paste the copied lines using **Command + V**. If the first `127.0.0.1 mongo1 mongo2 mongo3` line already exists, keep it just once. If old `.lab.test` lines exist, replace those old lines with the new ones.
7. Hold **Control** and press the letter **O**. This means “save”.
8. Press **Enter** to confirm the filename.
9. Hold **Control** and press **X** to close the editor.

**Use Control for saving/exiting, not Command.** You are opening a file here; do not type `cd /etc/hosts`.

### A8. Allow Docker to receive connections

1. Open **System Settings → Network → Firewall**.
2. If the firewall is on, open **Options**.
3. Make sure **Block all incoming connections** is not selected.
4. If **Docker** or **com.docker.backend** appears in the list, set it to **Allow incoming connections**. If macOS asks whether Docker may accept incoming connections, click **Allow**.

Leave the firewall switched on. See [Apple's firewall guide](https://support.apple.com/en-au/guide/mac-help/mh11783/mac) if your screen looks different.

**A is ready. Wait for B and C before clicking Connect.**

## Person B — Mac

### B1. Get the app and open the dashboard

1. Download **linked-lab-source.zip** from A.
2. Double-click the ZIP in Finder to unpack it. Open the resulting folder; you should see **Start Lab.command** and **README.md**. You may rename that folder **linked nodes and laptop**.
3. If this Mac has not run the app before, complete **First-time Mac installation** and **First-time Mac local entry** near the end of this guide.
4. Open **Docker Desktop** and wait for it to finish starting.
5. Double-click **Start Lab.command**. Keep the Terminal window open.
6. Open Safari or Chrome and visit **http://127.0.0.1:8502**.

**You should see:** **Disconnected · Local lab**. You are opening your own dashboard, not A's.

### B2. Join the team's hotspot

Use the Wi-Fi menu at the top of your Mac's screen. Select the **same hotspot name** as A and C, and enter the password given by the hotspot owner.

### B3. Find your IP address

1. Open **Apple menu → System Settings → Network → Wi-Fi → Details → TCP/IP** for the hotspot connection.
2. Find **IP address** or **IPv4 address**.
3. Put that number in the shared note as **Person B's IP**. Do not use the **Router** number or change settings here.
4. Wait until A and C have also added their IPs to the shared note.

### B4. Enter the addresses in your dashboard

Open **Connection setup · complete once on each laptop**.

| Field on your screen | What you put there |
| --- | --- |
| This laptop | Select **B** — change it from the initial A selection |
| Laptop A hotspot IPv4 | Copy **Person A's IP** from the shared note |
| Laptop B hotspot IPv4 | Copy **Person B's IP** from the shared note |
| Laptop C hotspot IPv4 | Copy **Person C's IP** from the shared note |

Only your **This laptop** selection differs from A's. All three address fields must match A's entries.

### B5. Load A's connection file

1. Download the **team.json** file A sent. Remember where you saved it, usually **Downloads**.
2. In your dashboard, find **Import shared team.json (B and C)**.
3. Click **Browse files** and select the downloaded **team.json**.
4. Click **Save connection settings**.

**You should see:** **Settings saved**, followed by a box containing address lines.

Do not click **Create team file on A**. Your team needs the same file from A.

### B6. Copy the address lines into your Mac

1. Copy the **whole address box** from your saved dashboard settings.
2. Open a new Terminal: **Command + Space → type Terminal → Enter**.
3. Paste and run:

   ```sh
   sudo nano /etc/hosts
   ```

4. Enter your **Mac login password** and press Enter. It is normal for the password to be invisible.
5. Move to the bottom with the arrow keys. Preserve the existing text and paste the copied lines with **Command + V**.
6. Keep the `127.0.0.1 mongo1 mongo2 mongo3` line only once. Replace any old `.lab.test` lines with the new ones.
7. Save: **Control + O**, then **Enter**. Exit: **Control + X**.

**Yes, B also keeps `mongo1 mongo2 mongo3` on the localhost line.** The app assigns your shared nodes mongo4–mongo6 automatically. Copy all the address lines, not just the line for B.

### B7. Allow Docker to receive connections

Open **System Settings → Network → Firewall → Options** if your firewall is on. Leave **Block all incoming connections** unselected. Allow incoming connections for **Docker / com.docker.backend** if listed, and click **Allow** if macOS shows a Docker connection prompt.

**B is ready. Wait for A and C before clicking Connect.**

## Person C — Windows

### C1. Get the app

1. Download **linked-lab-source.zip** from A.
2. Right-click it and select **Extract All**, then **Extract**.
3. Open the extracted folder. You should see **pyproject.toml**, **README.md**, and a folder named **scripts**. If you see only another folder, open that folder.

Do not double-click the Mac **Start Lab.command** file. You will start the app a different way on Windows.

### C2. Install the two tools the app needs

Skip a tool if it is already installed and working.

**Docker Desktop** runs the database:

1. Download and install it from [Docker's Windows installation page](https://docs.docker.com/desktop/setup/install/windows-install/).
2. Follow its installation prompts. Use **WSL 2** if asked which option to use; this is Docker's way of running the database on Windows. Restart the laptop if asked.
3. Open Docker Desktop and wait for it to finish starting.
4. If Docker's menu offers **Switch to Linux containers**, select it. If it offers **Switch to Windows containers**, you are already using the right mode; leave it alone.

**uv** installs the app's Python tools:

1. Open the Windows **Start** menu, type **PowerShell**, and open **Windows PowerShell**.
2. Paste this whole line and press Enter:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

3. Wait until it finishes, then close that PowerShell window. This is the installer from [uv's official guide](https://docs.astral.sh/uv/getting-started/installation/).

### C3. Add one line to your Windows address file

1. Open **Start**, search **Notepad**, right-click it, and select **Run as administrator**. Click **Yes** if Windows asks.
2. In Notepad, click **File → Open**.
3. Paste this exact path into the **File name** box and click **Open**:

   ```text
   C:\Windows\System32\drivers\etc\hosts
   ```

4. Keep existing text. At the bottom, add this line if it is missing:

   ```text
   127.0.0.1 mongo1 mongo2 mongo3
   ```

5. Press **Ctrl + S** to save. Close Notepad.

This file is named **hosts**, with no `.txt` at the end. If browsing for it instead, select **All Files** in the Open window so it is visible.

### C4. Find your IP address

1. Connect to the same hotspot as A and B using Windows' Wi-Fi menu.
2. Open **Start → Settings → Network & internet**.
3. Open **Properties** for your connected Wi-Fi network. On some Windows versions, click **Wi-Fi**, then the connected network's name.
4. Scroll down to **IPv4 address**.
5. Put that number in the shared note as **Person C's IP**. Do not use the **IPv4 DNS servers** or gateway values.

You are just reading the number; do not click Edit or change it. See [Microsoft's instructions](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/essential-network-settings-and-tasks-in-windows) if the layout differs.

### C5. Open the dashboard

1. Return to the extracted app folder in **File Explorer**. Make sure you can see **pyproject.toml**.
2. Click the **address bar at the top of File Explorer** — where the folder's location appears, not the search box.
3. Type `powershell` and press **Enter**. A PowerShell window opens in the correct folder.
4. Copy and run these lines **one at a time**, pressing Enter after each. Wait for one to finish before running the next:

   ```powershell
   $env:UV_CACHE_DIR = "$PWD/.uv-cache"
   ```

   ```powershell
   uv sync --locked
   ```

   ```powershell
   uv run --locked scripts/setup_lab.py --startup
   ```

5. The last command should say **Local lab ready**. If it shows an error, stop here and keep the error message.
6. Then run:

   ```powershell
   uv run --locked streamlit run scripts/app.py
   ```

7. Leave PowerShell running. Open Edge or Chrome and visit **http://127.0.0.1:8502**.

**You should see:** **Disconnected · Local lab**.

### C6. Enter the addresses and load A's file

Open **Connection setup · complete once on each laptop**.

| Field on your screen | What you put there |
| --- | --- |
| This laptop | Select **C** — change it from the initial A selection |
| Laptop A hotspot IPv4 | Copy **Person A's IP** from the shared note |
| Laptop B hotspot IPv4 | Copy **Person B's IP** from the shared note |
| Laptop C hotspot IPv4 | Copy **Person C's IP** from the shared note |

Then:

1. Download **team.json** from A, usually into **Downloads**.
2. Under **Import shared team.json (B and C)**, click **Browse files** and choose that file.
3. Click **Save connection settings**.

**You should see:** **Settings saved**, followed by a box containing address lines.

### C7. Copy the address lines into Windows

1. Copy the **whole address box** from your saved dashboard settings.
2. Open **Notepad as administrator** again, just as in C3.
3. Open this same file:

   ```text
   C:\Windows\System32\drivers\etc\hosts
   ```

4. Add the copied lines at the bottom. Keep existing unrelated text. Keep the `127.0.0.1 mongo1 mongo2 mongo3` line only once; replace old `.lab.test` lines if present.
5. Press **Ctrl + S** to save and close Notepad.

Copy **all** the lines. You do not need to rename anything to mongo7–mongo9 yourself.

### C8. Give A and B permission to connect to Windows

Windows may block connections from the other laptops. Do this once to allow your two friends:

1. Open **Start**, search **PowerShell**, right-click **Windows PowerShell**, and select **Run as administrator**. Click **Yes** if asked. Keep your dashboard's other PowerShell window running.
2. Paste this line and press Enter:

   ```powershell
   $labPeerA = Read-Host "Type Person A's IP from your shared note, then press Enter"
   ```

3. When it asks, type **A's actual IP from your shared note**, then press Enter.
4. Paste this line and press Enter:

   ```powershell
   $labPeerB = Read-Host "Type Person B's IP from your shared note, then press Enter"
   ```

5. When it asks, type **B's actual IP from your shared note**, then press Enter.
6. Paste this whole line **without changing it**, then press Enter:

   ```powershell
   New-NetFirewallRule -Name "LinkedMongoLab-C" -DisplayName "Linked Mongo lab - C" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 29023-29025 -RemoteAddress $labPeerA,$labPeerB -Profile Any
   ```

7. You should see details of the new rule. You can close **this administrator window**, leaving the dashboard window running.

This allows A and B to reach this lab; it leaves your firewall on. The numbers `29023-29025` are already chosen by the app, so do not replace them. See [Microsoft's firewall command reference](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule).

If Windows says the rule already exists, use the update instruction under **Next time you meet** instead.

**C is ready. Wait for A and B before clicking Connect.**

## Connect together — all three people

First, each person says yes to these four questions:

- Is my own dashboard open?
- Did I save the correct A/B/C selection and all three IPs?
- Did I copy the dashboard's address lines into my laptop's hosts file?
- Is Docker running, with the laptop still on the same hotspot?

Then:

1. A says **“Everyone click Connect now.”**
2. All three click **Connect** on their **own** dashboard. Do it at roughly the same time, within two minutes of each other.
3. **Do not wait for A's connection to finish before B/C click.** Everyone's nodes need to start together.
4. Keep the laptop screens awake and the dashboard windows open. Setup may take a few minutes.
5. Wait until **every dashboard** says **Connected · Shared cluster**, and the node list shows **one PRIMARY and eight SECONDARY**.

**That is the success check.** Seeing only three nodes means that laptop is still using its own local database.

## Check that you can share a document

### Everyone: use these settings

Scroll to the manual **Client A** panel on your own dashboard. Everyone can use that panel — its name is unrelated to which person you are.

| Box or option | What to enter/select |
| --- | --- |
| Database | `lab` |
| Collection | `manual` |
| Read concern | `majority` |
| Write concern | `majority` |
| Read from | `primary` |
| Causal consistency | Tick it |
| Upsert | Leave unticked |

### Person A: create the test document

1. Select **insert_one** in the write-operation dropdown.
2. Replace the text in **Write document / update** with this exact text:

   ```json
   {"_id":"team-check-001","message":"Hello friends"}
   ```

3. Click **Write** once. Check that it reports success.

### Persons B and C: find A's document

1. Replace the text in **Read filter** with:

   ```json
   {"_id":"team-check-001"}
   ```

2. Click **Read**.
3. Both should see **Hello friends**.

### Persons B and C: check that you can write too

Do this **one person at a time** — B first, then C.

1. Keep the same **Read filter** from the previous step.
2. Select **update_one**.
3. In **Write document / update**, B pastes:

   ```json
   {"$set":{"message":"Hello from B"}}
   ```

4. B clicks **Write**. A and C use the same Read filter and click **Read** to check they see **Hello from B**.
5. C repeats with:

   ```json
   {"$set":{"message":"Hello from C"}}
   ```

6. A and B click **Read** and check they see **Hello from C**.

You have now checked that all three laptops can read and write shared data.

If you already used `team-check-001` before, agree on `team-check-002` and replace the ID in **both the insert text and everyone's Read filter**. Do not delete your database to repeat the test.

## Finish for the day

1. Everyone finishes their writes. If experiments are running, click **Stop experiments** and wait until they finish recovering.
2. **A and B:** double-click **Stop Lab.command** in your own app folder.
3. **C:** click the PowerShell window running the dashboard, press **Ctrl + C**, then paste and run:

   ```powershell
   uv run --locked scripts/shutdown.py
   ```

Your saved data stays. Closing the browser alone does not stop the database.

To continue using your own local dashboard instead, click **Disconnect**. Tell your friends first because your shared nodes will stop. Local data and shared data are separate; switching does not copy documents between them.

## Next time you meet

1. Join the hotspot again and open Docker and your dashboard using the same launch steps.
2. Find each person's IP again using A3, B3, and C4. Wi-Fi can give a laptop a different address on another day.
3. If any IP changed, everyone updates the three dashboard IP boxes, clicks **Save connection settings**, and copies the updated address lines into their hosts file. Do this while disconnected.
4. If A or B's IP changed, C repeats the two IP prompts in C8, then runs this **instead of** the `New-NetFirewallRule` line:

   ```powershell
   Get-NetFirewallRule -Name "LinkedMongoLab-C" | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $labPeerA,$labPeerB
   ```

5. Keep your existing team file and saved person selection. You do not need to download/import the team file again or reinstall tools.
6. Click Connect together again.

## First-time Mac installation — only if needed

Do this on a Mac that has not run the app before.

**Install Docker Desktop:**

1. Click **Apple menu → About This Mac**. If it says **Chip: Apple M…**, choose the **Apple silicon** download. If it says **Processor: Intel…**, choose **Intel**.
2. Download the matching version from [Docker's Mac installation page](https://docs.docker.com/desktop/setup/install/mac-install/).
3. Open the downloaded file and drag **Docker** into **Applications** when shown.
4. Open Docker from Applications and complete its setup prompts.

**Install uv:**

1. Press **Command + Space**, type **Terminal**, and press Enter.
2. Paste this line and press Enter:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Wait until installation finishes, then close Terminal and open a new one. See [uv's installer instructions](https://docs.astral.sh/uv/getting-started/installation/) if it reports an error.

Continue with the local entry below, then return to A2 or B1 to start the dashboard.

## First-time Mac local entry — only if missing

1. Open Terminal using **Command + Space → Terminal → Enter**.
2. Paste and run:

   ```sh
   sudo nano /etc/hosts
   ```

3. Type your Mac login password and press Enter. It is invisible while you type.
4. Keep the existing lines. Move to the bottom with the arrow keys and add this if missing:

   ```text
   127.0.0.1 mongo1 mongo2 mongo3
   ```

5. Press **Control + O**, then **Enter**, then **Control + X**.

Both Macs need the same line. You will add the friends' address lines later after saving your dashboard settings.

## If something goes wrong

| What happened? | What to do |
| --- | --- |
| Mac says Start Lab.command does not have permission | Open Terminal in the app folder using A1's `cd` and drag steps. Run `chmod +x "Start Lab.command" "Stop Lab.command"`, then try again. |
| Windows cannot find uv | Close and reopen PowerShell after installing uv, then repeat C5. |
| Windows cannot find pyproject.toml | Repeat C5 from the folder where you can actually see that file. |
| The dashboard does not open | Look at the launcher Terminal/PowerShell window. Keep the full error text; do not continue with connection setup until local startup works. |
| The app asks for a hosts entry | Repeat your address-file step. Save the file as well as the dashboard settings; they are two separate actions. |
| Saving says the identity is fixed or the team is different | Check you selected your assigned person and received the clean ZIP from A. Do not delete private settings to bypass this error. |
| Connect keeps waiting or fails | Check all three clicked Connect and still have Docker open. Recheck the three IPs, copied address lines, and firewall steps. Some hotspots block laptops from contacting each other; another hotspot or router may be needed. |
| Connection failed and Return to local mode appears | Click it and wait. Fix the reported issue, have everyone return to local mode, then retry Connect together. |
| You only see three nodes | You are still disconnected. Do not use a local write as your shared-data test. |
| A friend cannot find the document | Check everyone says Connected and uses database `lab`, collection `manual`, and exactly the same ID. Confirm A's Write succeeded, then try Read again after a short wait. |
| The insert says duplicate key | That ID already exists. Use the new-ID instructions below the test. |

If you need help, send **which person (A/B/C), which step number, and the complete error message**. Do not send the contents of `team.json`.

For now, keep database **lab**. Later you can agree on a different collection name, such as **team_demo**, and enter it in everyone's **Collection** box. Using a different shared database requires an administrator to grant access first.

The Mac local startup has been checked. Your actual two-Mac/one-Windows connection still needs the shared-data test above. Additional technical details are in [README.md](README.md).
