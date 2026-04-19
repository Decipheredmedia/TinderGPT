<div align="center"><img src="/images/readme.gif" width="650" ></div>

# TinderGPT 
### Your automatic dating assistant

TinderGPT automates the process of writing and arranging dates with girls on Tinder, enabling you to generate romantic meetings with almost zero effort. Your only role is to like the profiles that catch your eye. After that, TinderGPT comes into the play. It initiates a conversation with the girl, using details from her profile, continues by building an emotional bond and highlighting your attractive traits, and finishes by arranging a meeting and giving you a push-up on your phone with her number.


## Prerequisites

| Requirement | Minimum |
|---|---|
| **OS** | Ubuntu 20.04 LTS 64-bit (also works on other Linux, macOS, Windows) |
| **Python** | 3.8 or newer |
| **RAM** | 2 GB (the VPS runner enforces an 80 % ceiling) |
| **Disk** | 40 GB SSD (the VPS runner enforces a 90 % ceiling) |
| **Browser** | Firefox (with geckodriver on `$PATH`) |
| **Network** | Port 25 is **blocked** — SMTP uses 587 (STARTTLS) or 465 (SSL) |

System packages (Ubuntu 20.04):

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip firefox geckodriver
```


## Installation

### 1. Clone the repository

```bash
git clone https://github.com/GregorD1A1/TinderGPT
cd TinderGPT
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv env
source env/bin/activate
```

### 3. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Create a Firefox profile for Selenium

1. Open Firefox and type `about:profiles` in the address bar.
2. Click **Create a New Profile**.
3. Set the profile directory to `<repo>/driver/FirefoxProfile`.
4. Set your *old* profile as default again, then launch the **new** profile.
5. In the new profile window navigate to `tinder.com` and log in. Close any pop-ups (Gold offers, location permissions, etc.).

### 5. Configure environment variables

```bash
cp .env.template .env
```

Open `.env` in a text editor and fill in **at least** the required fields:

| Variable | Required | Description |
|---|---|---|
| `USE_LANGUAGE` | ✅ | Language for messages (e.g. `English`, `Polish`) |
| `CITY` | ✅ | Your city name |
| `PERSONALITY` | ✅ | 3–4 sentences about yourself |
| `OPENAI_API_KEY` | ✅ | API key from [platform.openai.com](https://platform.openai.com/) |
| `AIRTABLE_TOKEN` | ✅ | Personal access token from Airtable Developer Hub |
| `AIRTABLE_WORKSPACE_ID` | ✅ | Found in the Airtable URL (`app…`) |
| `PUSHBULLET_API_KEY` | — | For phone push notifications |

**VPS-specific variables** (used only by `vps_runner.py`):

| Variable | Default | Description |
|---|---|---|
| `VPS_SERVER_HOST` | `127.0.0.1` | FastAPI bind address |
| `VPS_SERVER_PORT` | `8080` | FastAPI bind port |
| `VPS_RAM_THRESHOLD` | `80` | Max RAM % before pausing |
| `VPS_DISK_THRESHOLD` | `90` | Max disk % before pausing |
| `VPS_RESOURCE_CHECK_INTERVAL` | `30` | Seconds between resource checks |
| `VPS_SERVER_STARTUP_TIMEOUT` | `120` | Seconds to wait for server readiness |
| `SESSION1_HOUR_START` | `17` | Session 1 start hour (24 h) |
| `SESSION1_HOUR_END` | `18` | Session 1 end hour |
| `SESSION2_HOUR_START` | `18` | Session 2 start hour |
| `SESSION2_HOUR_END` | `19` | Session 2 end hour |
| `SESSION3_HOUR_START` | `20` | Session 3 start hour |
| `SESSION3_HOUR_END` | `21` | Session 3 end hour |

**SMTP email notifications** (optional — port 25 is blocked):

| Variable | Default | Description |
|---|---|---|
| `SMTP_ENABLED` | `false` | Set to `true` to enable email alerts |
| `SMTP_HOST` | | SMTP server hostname |
| `SMTP_PORT` | `587` | Use `587` (STARTTLS) or `465` (SSL). **Never** `25`. |
| `SMTP_USER` | | SMTP login username |
| `SMTP_PASSWORD` | | SMTP login password |
| `SMTP_FROM` | | Sender email address |
| `SMTP_TO` | | Recipient email address |


## Usage

### PC — interactive mode

```bash
source env/bin/activate
python main.py --head
```

Then in a browser:

- `http://localhost:8080/start_tnd` — open Tinder
- `http://localhost:8080/opener` — send an opener to the latest match
- `http://localhost:8080/respond` — respond to the first unread message
- `http://localhost:8080/respond_all` — respond to all unread messages

### VPS — fully automated (single command)

```bash
source env/bin/activate
python vps_runner.py
```

This single command:

1. Checks system resources (RAM < 80 %, disk < 90 %).
2. Starts the TinderGPT FastAPI server in headless mode.
3. Schedules three daily sessions at random times within configurable hour ranges.
4. Processes **every** conversation endpoint — nothing is skipped.
5. Logs every action with `SUCCESS` / `FAILURE` to `logs/vps_runner.log`.
6. Sends email notifications on failures (if SMTP is enabled).
7. Automatically pauses when resources are strained and resumes when they drop.
8. Restarts the server if it crashes.
9. Shuts down gracefully on `Ctrl+C` or `SIGTERM`.

**Tip:** Run inside `tmux` or as a systemd service so the process survives SSH disconnects:

```bash
# tmux
tmux new -s tindergpt
source env/bin/activate && python vps_runner.py
# Detach with Ctrl+B, D

# systemd (create /etc/systemd/system/tindergpt.service)
# [Unit]
# Description=TinderGPT VPS Runner
# After=network.target
#
# [Service]
# User=ubuntu
# WorkingDirectory=/home/ubuntu/TinderGPT
# ExecStart=/home/ubuntu/TinderGPT/env/bin/python vps_runner.py
# Restart=on-failure
# RestartSec=30
#
# [Install]
# WantedBy=multi-user.target
```

### Raspberry Pi

See the original instructions below.  On an RPi 4 (4 GB+) you can use `vps_runner.py` the same way as on a VPS.


## Raspberry Pi installation (legacy)

1. You need at least RPi 4 with 4 GB RAM.
2. Install Ubuntu desktop (Raspberry Pi Imager).
3. Follow PC installation steps 1–7 to create the Firefox profile.
4. Copy your `.env` from PC or fill in steps 8–12 of PC installation.

### Raspberry Pi usage (legacy)

```bash
source env/bin/activate
python main.py          # terminal 1
python scheduler.py     # terminal 2
```


## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'psutil'` | `pip install -r requirements.txt` inside the venv |
| Server never becomes ready (timeout) | Check Firefox/geckodriver are installed: `firefox --version && geckodriver --version` |
| `SMTP_PORT=25 is blocked` error | Change `SMTP_PORT` to `587` or `465` in `.env` |
| High RAM usage causes pausing | Lower `VPS_RAM_THRESHOLD` or reduce session endpoints |
| Log file missing | The `logs/` directory is auto-created on first run |
| `ConnectionRefusedError` when calling endpoints | Make sure `main.py` is not already running on the same port |
| Tinder pop-ups block Selenium | Log in manually once with the Firefox profile and dismiss all pop-ups |
| `geckodriver` not found | `sudo apt install geckodriver` or download from [github.com/mozilla/geckodriver](https://github.com/mozilla/geckodriver/releases) |
| Airtable errors | Verify `AIRTABLE_TOKEN` and `AIRTABLE_WORKSPACE_ID` in `.env` |
| OpenAI rate limits | The script retries up to 3 times with exponential back-off |


## AI dating good practices
When going to date organized by TinderGPT, I recommend you to tell your match, that it was picked up by artificial intellegence. Beyond the fun value of the situation, it's a good practice to inform users that they are speaking with a bot.


## Contribution
While application is already working, there still a lot of things to improve. I'm appreciate if you want to contribute to the project. 

While improving prompts, pick-up rules knowledge base or scripts in AI_logic folder, use `localhost:8080/reload` to reload changes immidiatelly without restarting whole the application (which is time-consuming).