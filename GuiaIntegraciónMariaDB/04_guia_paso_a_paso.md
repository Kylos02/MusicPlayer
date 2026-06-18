# Implementation Guide — MariaDB on YOUR PC, Node-RED on the Raspberry Pi

## Architecture

```text
RASPBERRY PI                        YOUR PC
┌───────────────────┐        ┌───────────────────────┐
│ Python (p.py)     │        │  MariaDB (port 3306)  │
│     ↓ MQTT        │  LAN   │                       │
│  Mosquitto        │ ─────▶ │  HeidiSQL / DBeaver   │
│     ↓             │        │  (GUI client)         │
│  Node-RED         │        │                       │
└───────────────────┘        └───────────────────────┘
       192.168.x.A                  192.168.x.B
```

## Prerequisites

- Raspberry Pi and PC **on the same Wi-Fi network** (same router).
- Write down your PC's local IP address. You will use it in step 5.

---

# STEP 1 · Install MariaDB on YOUR PC

Follow the section corresponding to your operating system.

## 1A · Windows

1. Download the MariaDB installer: https://mariadb.org/download
2. Run the `.msi` file. In the setup wizard:
   - Set a **root** password (make sure to save it).
   - Check ✅ **"Enable access from remote machines for 'root' user"** — this is NOT for Node-RED to use root, it is to ensure the service accepts external connections.
   - Leave the default port as **3306**.
   - Leave checked **"Use UTF8 as default server character set"**.
3. Once finished, the "MariaDB" service will already be running. **HeidiSQL** (a GUI client) is included — this is what you will use to view the data.

## 1B · macOS (Homebrew)

```bash
brew install mariadb
brew services start mariadb

# Secure the installation and set a root password:
sudo mariadb-secure-installation
```

As a GUI client, I recommend **DBeaver Community** (free, download from dbeaver.io) or **TablePlus**.

## 1C · Linux (Ubuntu / Debian / Fedora)

```bash
sudo apt update && sudo apt install -y mariadb-server
sudo systemctl enable --now mariadb
sudo mariadb-secure-installation
```

As a GUI client: **DBeaver Community**.

---

# STEP 2 · Configure MariaDB to accept remote connections

By default, MariaDB **only accepts connections from the PC itself** (`localhost`). We need to change this.

## 2A · Windows

1. Open the configuration file:
   `C:\Program Files\MariaDB XX.X\data\my.ini`
   (Open it with Notepad as **Administrator**)

2. Find the `[mysqld]` section and look for this line:
   ```ini
   bind-address=127.0.0.1
   ```
   Change it to:
   ```ini
   bind-address=0.0.0.0
   ```
   If it doesn't exist, add it below `[mysqld]`.

3. Save the file.

4. Restart the service: open **services.msc** → find **"MariaDB"** → right-click → **Restart**.

## 2B · macOS

```bash
# Edit the config file:
nano /opt/homebrew/etc/my.cnf        # Apple Silicon (M1/M2/M3)
# or:
nano /usr/local/etc/my.cnf           # Intel
```

In the `[mysqld]` section, add (or change):
```ini
bind-address = 0.0.0.0
```

Save (`Ctrl+O`, `Enter`, `Ctrl+X`) and restart:
```bash
brew services restart mariadb
```

## 2C · Linux

```bash
sudo nano /etc/mysql/mariadb.conf.d/50-server.cnf
```

Find `bind-address = 127.0.0.1` and change it to:
```ini
bind-address = 0.0.0.0
```

Restart:
```bash
sudo systemctl restart mariadb
```

## Verify that it is listening on the network

On your PC, run:

**Windows (PowerShell):**
```powershell
netstat -an | findstr 3306
```
It should show `0.0.0.0:3306` (not `127.0.0.1:3306`).

**macOS / Linux:**
```bash
ss -tlnp | grep 3306
```
It should also show `0.0.0.0:3306`.

---

# STEP 3 · Open port 3306 in the firewall

## 3A · Windows

1. Open **"Windows Defender Firewall with Advanced Security"** (search for it in the Start menu).
2. Click on **"Inbound Rules"** → **"New Rule..."**
3. Rule Type: **Port** → Next.
4. **TCP**, specific local ports: **3306** → Next.
5. **Allow the connection** → Next.
6. Check only **"Private"** (home network) — DO NOT check "Public" → Next.
7. Name: `MariaDB`. Finish.

## 3B · macOS

By default, the firewall does not block incoming connections unless it is active. If it is active:
**System Preferences → Network → Firewall → Firewall Options → +** and add `mariadbd` allowing incoming connections.

## 3C · Linux (ufw)

```bash
# Allow only from the local network (safer):
sudo ufw allow from 192.168.0.0/16 to any port 3306 proto tcp
# or open to anyone:
# sudo ufw allow 3306/tcp
```

---

# STEP 4 · Run the SQL script on YOUR PC

1. Open `01_setup_mariadb.sql` and **change the password** `CAMBIAME_POR_UNA_SEGURA` to a real, secure password. Save it.

2. If you want more security, also change the line:
   ```sql
   CREATE USER IF NOT EXISTS 'nodered'@'192.168.%.%'
   ```
   to your Raspberry Pi's specific IP:
   ```sql
   CREATE USER IF NOT EXISTS 'nodered'@'192.168.1.50'   -- ← your real IP
   ```
   (And update the same IP in the `GRANT` line.)

3. Execute the script:

   - **Windows**: open **HeidiSQL** → connect to `localhost` with root → `File → Load SQL file…` → select `01_setup_mariadb.sql` → run (F9).
   - **macOS/Linux**: `mariadb -u root -p < 01_setup_mariadb.sql`

At the end, you should see a row in `mysql.user` with `user=nodered host=192.168.%.%`.

---

# STEP 5 · Find out your PC's IP on the network

## Windows
```powershell
ipconfig
```
Look for **"IPv4 Address"** under your Wi-Fi adapter. It should look like `192.168.1.100`.

## macOS
```bash
ipconfig getifaddr en0
```

## Linux
```bash
hostname -I
```

**Write down this IP** — you will need to provide it to Node-RED.

## Test from the Raspberry Pi

From an SSH terminal on your Pi:
```bash
# Is there port connectivity?
# It should say "succeeded"
nc -zv 192.168.x.x 3306

# Can we authenticate?
# enter the password you assigned
sudo apt install -y mariadb-client     # if you didn't have it
mariadb -h 192.168.x.x -u nodered -p
```

If it enters the `MariaDB [(none)]>` prompt, the connection is successful. Type `exit` to quit.

**If it fails**, the cause is usually one of three things: `bind-address` was not set to 0.0.0.0 (Step 2), the firewall is blocking it (Step 3), or the user doesn't have the correct host pattern (check with `SELECT user, host FROM mysql.user;`).

---

# STEP 6 · Install Node-RED on the Raspberry Pi (if you haven't)

```bash
bash <(curl -sL [https://raw.githubusercontent.com/node-red/linux-installers/master/deb/update-nodejs-and-nodered](https://raw.githubusercontent.com/node-red/linux-installers/master/deb/update-nodejs-and-nodered))
sudo systemctl enable --now nodered
```

Go to `http://<raspberry-ip>:1880` from your PC's browser.

Menu (☰) → **Manage palette** → **Install** → search for `node-red-node-mysql` → Install.

---

# STEP 7 · Import the flow and configure the IP

1. In Node-RED: menu (☰) → **Import** → select `03_nodered_flow.json`.
2. A new tab **"Reproductor - Historial"** will be created.
3. **Before clicking Deploy**, open the **"INSERT reproducciones"** node (double-click) → edit the `MariaDB reproductor` config (pencil icon ✏️):
   - **Host**: ⚠️ YOUR PC's IP (e.g., `192.168.1.100`) — **NOT localhost**
   - **Port**: `3306`
   - **User**: `nodered`
   - **Password**: the one you set in Step 4
   - **Database**: `reproductor`
   - **Charset**: `UTF8MB4_UNICODE_CI` (if not available, use `UTF8MB4`)
4. Done → **Deploy**.

The MySQL node should display a **green "connected"** status. If it turns red, go back to Step 5 and ensure the manual connection works.

---

# STEP 8 · Minor change in Python

Open `p.py` on the Raspberry Pi, look for the `_hilo_publicar` function (around line 835). Inside the `if` block that publishes to `MQTT_TOPIC_META`, add `"duracion": snap["duracion"]` to the JSON:

```python
# Before:
json.dumps({"titulo": snap["titulo"], "artista": snap["artista"],
            "album": snap["album"], "fuente": snap["fuente"]},
           ensure_ascii=False),

# After:
json.dumps({"titulo": snap["titulo"], "artista": snap["artista"],
            "album": snap["album"], "fuente": snap["fuente"],
            "duracion": snap["duracion"]},
           ensure_ascii=False),
```

Save and restart the script.

---

# STEP 9 · Test everything together

1. Play a song (Spotify/Airplay/local) from the Raspberry Pi.
2. In Node-RED, you will see:
   - `Validar + Dedupe` node → blue status with `→ <título>`
   - `INSERT reproducciones` node → green status
   - `Log OK` node → `id=N ✓`
3. On your PC, open HeidiSQL (or DBeaver) → connect to `localhost`, user `root`, your password → open the `reproductor` database → `reproducciones` table.

The song you just played should appear there. 🎉

---

# What happens if your PC is off

When your PC is turned off, Node-RED **will not be able to connect**, and messages generated during that time will be lost (the MySQL node will show an error in the Debug panel). This is acceptable for a standard project.

If you want to **never lose a playback record**, there are two approaches:

1. **Local SQLite buffer on the Raspberry Pi**: install `node-red-node-sqlite`, change the flow to insert data into a local SQLite DB first, and use a second flow with a timer to copy data from SQLite to MariaDB and delete synced rows when the connection is available.
2. **Queue gate in Node-RED**: `node-red-contrib-queue-gate` accumulates messages when the MySQL connection fails and retries them later.

Let me know if you want the buffer flow and I will build it for you.

---

# Quick Troubleshooting

| Symptom | Most likely cause | Solution |
|---|---|---|
| `nc -zv` fails with "Connection refused" | `bind-address` is still set to 127.0.0.1 | Step 2 |
| `nc -zv` hangs, no response | Firewall is blocking | Step 3 |
| `mariadb -h ... -u nodered` gives "Access denied" | The user does not allow your IP | Step 4: check the user's host pattern with `SELECT user, host FROM mysql.user;` |
| Node-RED MySQL is red, says ETIMEDOUT | Incorrect IP or PC is turned off/suspended | Check with `ping <pc_ip>` |
| `???` characters instead of accents | MySQL node charset is misconfigured | Set `UTF8MB4` in the node config |
| Inserts duplicates when restarting Node-RED | It catches the first MQTT `retain` message, but dedupe catches it in <1s | Normal behavior; ensure the Dedupe node status says "duplicado" |

---

# Tip: Static IP for your PC

If your router assigns IPs via DHCP, your PC's IP might change (after a reboot or several days). Two options:

1. **DHCP Reservation on the router**: log into your router's config (usually `192.168.1.1`), find the DHCP section, and "reserve" the current IP to your PC's MAC address.
2. **Static IP on your PC**: configure it directly in your network adapter settings. More prone to conflicts if you don't know what you are doing.

Option 1 is cleaner. If your IP changes, you just need to update the node in Node-RED.