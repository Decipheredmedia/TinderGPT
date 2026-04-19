#!/usr/bin/env python3
"""
vps_runner.py — VPS-optimized automated runner for TinderGPT

Compatible with Ubuntu 20.04 (64-bit), Python 3.8+
Designed for: 2 CPU cores, 2 GB RAM, 40 GB SSD, 2 TB bandwidth

Features
--------
* Resource monitoring — pauses / exits when RAM ≥ 80 % or disk ≥ 90 %
* Complete conversation processing — every endpoint is called, every item
  logged with SUCCESS / FAILURE status (nothing is skipped)
* SMTP notifications via port 587 (STARTTLS) or 465 (SSL) — port 25 is
  never used
* Retry logic with exponential back-off on every network call
* Rotating log file (5 MB × 5 backups) under ``logs/``
* Graceful shutdown on SIGINT / SIGTERM
* Server lifecycle management (auto-start, health-check, restart)
* Fully automated — zero manual intervention after ``python vps_runner.py``

Usage
-----
    python vps_runner.py          # headless (default for VPS)
    python vps_runner.py --head   # with browser window (for debugging)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import signal
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psutil
import requests
import schedule
from dotenv import load_dotenv, find_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

# ── Configuration ─────────────────────────────────────────────────────────

load_dotenv(find_dotenv())

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "vps_runner.log"

# Server
SERVER_HOST = os.getenv("VPS_SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("VPS_SERVER_PORT", "8080"))
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"

# Resource thresholds
RAM_THRESHOLD_PCT = float(os.getenv("VPS_RAM_THRESHOLD", "80"))
DISK_THRESHOLD_PCT = float(os.getenv("VPS_DISK_THRESHOLD", "90"))
RESOURCE_CHECK_INTERVAL = int(os.getenv("VPS_RESOURCE_CHECK_INTERVAL", "30"))

# SMTP — port 25 is blocked on the VPS; use 587 (STARTTLS) or 465 (SSL)
SMTP_ENABLED = os.getenv("SMTP_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_TO = os.getenv("SMTP_TO", "")

# Session configuration (hours are configurable via env vars)
SESSION_CONFIG = {
    "session1": {
        "time_range": (
            int(os.getenv("SESSION1_HOUR_START", "17")),
            int(os.getenv("SESSION1_HOUR_END", "18")),
        ),
        "endpoints": ["/start_tnd", "/respond_all", "/close"],
    },
    "session2": {
        "time_range": (
            int(os.getenv("SESSION2_HOUR_START", "18")),
            int(os.getenv("SESSION2_HOUR_END", "19")),
        ),
        "endpoints": [
            "/start_tnd",
            "/respond_all",
            "/opener",
            "/opener",
            "/opener",
            "/close",
        ],
    },
    "session3": {
        "time_range": (
            int(os.getenv("SESSION3_HOUR_START", "20")),
            int(os.getenv("SESSION3_HOUR_END", "21")),
        ),
        "endpoints": [
            "/start_tnd",
            "/respond_all",
            "/rise",
            "/clear_base",
            "/close",
        ],
    },
}

# Server startup
SERVER_STARTUP_TIMEOUT = int(os.getenv("VPS_SERVER_STARTUP_TIMEOUT", "120"))

# ── Logging ───────────────────────────────────────────────────────────────

logger = logging.getLogger("vps_runner")
logger.setLevel(logging.DEBUG)

_file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)

logger.addHandler(_file_handler)
logger.addHandler(_console_handler)

# ── Globals ───────────────────────────────────────────────────────────────

_server_process: subprocess.Popen | None = None
_shutdown_requested = False
_scheduled_jobs: dict = {}

# ── Signal handling ───────────────────────────────────────────────────────


def _handle_signal(signum, _frame):
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info("Received %s — initiating graceful shutdown", sig_name)
    _shutdown_requested = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ── Resource monitoring ───────────────────────────────────────────────────


def check_resources() -> tuple:
    """Return ``(ok, details_dict)``.  *ok* is False when any threshold is breached."""
    mem = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    ram_pct = mem.percent
    disk_pct = (disk.used / disk.total) * 100

    details = {
        "ram_percent": round(ram_pct, 1),
        "ram_used_mb": round(mem.used / (1024**2)),
        "ram_total_mb": round(mem.total / (1024**2)),
        "disk_percent": round(disk_pct, 1),
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
    }

    ok = True
    if ram_pct >= RAM_THRESHOLD_PCT:
        logger.warning(
            "RAM usage %.1f%% exceeds threshold %.1f%% — pausing",
            ram_pct,
            RAM_THRESHOLD_PCT,
        )
        ok = False
    if disk_pct >= DISK_THRESHOLD_PCT:
        logger.warning(
            "Disk usage %.1f%% exceeds threshold %.1f%% — pausing",
            disk_pct,
            DISK_THRESHOLD_PCT,
        )
        ok = False

    return ok, details


def wait_for_resources(timeout: int = 300) -> bool:
    """Block until resources drop below thresholds or *timeout* expires.

    Returns ``True`` when resources are available, ``False`` on timeout.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        ok, details = check_resources()
        if ok:
            return True
        logger.info(
            "Waiting for resources — RAM %.1f%%, Disk %.1f%%",
            details["ram_percent"],
            details["disk_percent"],
        )
        time.sleep(RESOURCE_CHECK_INTERVAL)
    logger.error("Resource wait timed out after %ds", timeout)
    return False


# ── SMTP notifications (port 25 blocked — use 587 or 465) ────────────────


def send_email_notification(subject: str, body: str) -> None:
    """Send an email notification.  Never uses port 25."""
    if not SMTP_ENABLED:
        logger.debug("SMTP disabled — skipping email notification")
        return
    if SMTP_PORT == 25:
        logger.error(
            "Port 25 is blocked on this VPS. Set SMTP_PORT to 587 or 465."
        )
        return

    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = SMTP_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # 587 with STARTTLS (or any other non-25 port)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        logger.info("Email notification sent: %s", subject)
    except Exception:
        logger.exception("Failed to send email notification")


# ── Server management ────────────────────────────────────────────────────


def start_server(headless: bool = True) -> bool:
    """Start the TinderGPT FastAPI server as a subprocess."""
    global _server_process
    if _server_process is not None and _server_process.poll() is None:
        logger.info("Server already running (PID %d)", _server_process.pid)
        return True

    cmd = [sys.executable, str(BASE_DIR / "main.py")]
    if not headless:
        cmd.append("--head")

    logger.info("Starting TinderGPT server: %s", " ".join(cmd))
    _server_process = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logger.info("Server process started (PID %d)", _server_process.pid)

    # Wait for the server to respond
    deadline = time.monotonic() + SERVER_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _shutdown_requested:
            stop_server()
            return False
        try:
            resp = requests.get(f"{BASE_URL}/", timeout=5)
            if resp.status_code == 200:
                logger.info("Server is ready")
                return True
        except requests.ConnectionError:
            pass
        time.sleep(2)

    logger.error("Server did not become ready within %ds", SERVER_STARTUP_TIMEOUT)
    stop_server()
    return False


def stop_server() -> None:
    """Gracefully stop the TinderGPT server subprocess."""
    global _server_process
    if _server_process is None:
        return
    if _server_process.poll() is not None:
        logger.info("Server already exited (code %d)", _server_process.returncode)
        _server_process = None
        return

    pid = _server_process.pid
    logger.info("Stopping server (PID %d)", pid)
    _server_process.terminate()
    try:
        _server_process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        logger.warning("Server did not stop in time — killing PID %d", pid)
        _server_process.kill()
        _server_process.wait(timeout=5)
    logger.info("Server stopped")
    _server_process = None


def server_is_alive() -> bool:
    """Check whether the server subprocess is still running."""
    return _server_process is not None and _server_process.poll() is None


# ── Endpoint calls with retry & logging ──────────────────────────────────


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=5, min=5, max=60),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _call_endpoint(endpoint: str) -> tuple:
    """Call a TinderGPT server endpoint with retries.

    Returns ``(status_code, response_text)``.
    """
    url = BASE_URL + endpoint
    logger.debug("GET %s", url)
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    logger.info("Endpoint %s returned HTTP %d", endpoint, resp.status_code)
    return resp.status_code, resp.text


def process_endpoint(endpoint: str) -> bool:
    """Process a single endpoint with resource checks, retries, and logging.

    Returns ``True`` on success, ``False`` on failure (never raises).
    """
    # Pre-flight resource check
    ok, _details = check_resources()
    if not ok:
        logger.warning("Resources strained before %s — waiting", endpoint)
        if not wait_for_resources():
            logger.error("Aborting %s — resources not available", endpoint)
            return False

    try:
        status, _text = _call_endpoint(endpoint)
        logger.info("PROCESSED endpoint=%s status=SUCCESS http=%d", endpoint, status)
        return True
    except Exception:
        logger.exception("PROCESSED endpoint=%s status=FAILURE", endpoint)
        return False


# ── Session runner ────────────────────────────────────────────────────────


def run_session(session_name: str) -> None:
    """Execute every endpoint for *session_name* sequentially.

    No endpoint is skipped — each is logged with SUCCESS or FAILURE.
    """
    config = SESSION_CONFIG[session_name]
    endpoints = config["endpoints"]
    logger.info(
        "=== Starting %s (%d endpoints) ===", session_name, len(endpoints)
    )

    results: list = []
    for idx, endpoint in enumerate(endpoints, 1):
        if _shutdown_requested:
            logger.info("Shutdown requested — aborting session %s", session_name)
            break

        logger.info(
            "[%s %d/%d] Processing %s",
            session_name,
            idx,
            len(endpoints),
            endpoint,
        )
        success = process_endpoint(endpoint)
        results.append({"endpoint": endpoint, "success": success})

        # Human-like delay between endpoints
        if idx < len(endpoints):
            delay = random.uniform(2, 8)
            logger.debug("Sleeping %.1fs between endpoints", delay)
            time.sleep(delay)

    # Summary
    total = len(results)
    succeeded = sum(1 for r in results if r["success"])
    failed = total - succeeded
    logger.info(
        "=== %s complete: %d/%d succeeded, %d failed ===",
        session_name,
        succeeded,
        total,
        failed,
    )

    if failed > 0:
        failures = [r["endpoint"] for r in results if not r["success"]]
        send_email_notification(
            f"TinderGPT {session_name}: {failed} endpoint(s) failed",
            f"Session: {session_name}\n"
            f"Time: {datetime.now().isoformat()}\n"
            f"Total: {total}, Succeeded: {succeeded}, Failed: {failed}\n"
            f"Failed endpoints: {', '.join(failures)}\n",
        )

    # Re-schedule for next day
    if session_name in _scheduled_jobs:
        schedule.cancel_job(_scheduled_jobs[session_name])
    _schedule_session(session_name)


def _schedule_session(session_name: str) -> None:
    """Schedule *session_name* at a random minute within its hour range."""
    time_range = SESSION_CONFIG[session_name]["time_range"]
    minute = random.randint(0, 59)
    hour = random.randint(time_range[0], time_range[1] - 1)
    session_time = f"{hour:02d}:{minute:02d}"
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(
        "%s scheduled for %s (next occurrence ~%s)",
        session_name,
        session_time,
        tomorrow,
    )
    job = schedule.every().day.at(session_time).do(run_session, session_name)
    _scheduled_jobs[session_name] = job


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VPS-optimized automated runner for TinderGPT"
    )
    parser.add_argument(
        "--head",
        action="store_true",
        help="Run the browser in visible (non-headless) mode for debugging",
    )
    return parser.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    args = _parse_args()
    headless = not args.head

    logger.info("=" * 60)
    logger.info("TinderGPT VPS Runner starting")
    logger.info("Python %s on %s", sys.version.split()[0], sys.platform)
    logger.info("Base dir: %s", BASE_DIR)
    logger.info("Log file: %s", LOG_FILE)
    logger.info("Server: %s (headless=%s)", BASE_URL, headless)
    logger.info(
        "Resource thresholds: RAM < %.0f%%, Disk < %.0f%%",
        RAM_THRESHOLD_PCT,
        DISK_THRESHOLD_PCT,
    )
    logger.info(
        "SMTP: %s (port %d)", "enabled" if SMTP_ENABLED else "disabled", SMTP_PORT
    )
    logger.info("=" * 60)

    # Validate SMTP port
    if SMTP_ENABLED and SMTP_PORT == 25:
        logger.error(
            "SMTP_PORT=25 is blocked on this VPS. Set SMTP_PORT to 587 or 465."
        )
        sys.exit(1)

    # Initial resource check
    ok, details = check_resources()
    logger.info("Initial resources: %s", json.dumps(details))
    if not ok:
        logger.error("Resources already exceeded thresholds at startup — exiting")
        sys.exit(1)

    # Start server
    if not start_server(headless=headless):
        logger.error("Failed to start TinderGPT server — exiting")
        sys.exit(1)

    # Schedule sessions
    for session_name in SESSION_CONFIG:
        _schedule_session(session_name)

    logger.info("Scheduler running — press Ctrl+C to stop")

    send_email_notification(
        "TinderGPT VPS Runner started",
        f"Server started at {datetime.now().isoformat()}\nURL: {BASE_URL}",
    )

    # Main loop
    try:
        while not _shutdown_requested:
            schedule.run_pending()

            # Periodic resource check
            ok, details = check_resources()
            if not ok:
                logger.warning("Resources strained — pausing scheduler loop")
                if not wait_for_resources(timeout=600):
                    logger.error("Resources not recovered — shutting down")
                    break

            # Server health check
            if not server_is_alive():
                logger.warning("Server process died — attempting restart")
                if not start_server(headless=headless):
                    logger.error("Server restart failed — shutting down")
                    break

            time.sleep(30)
    except Exception:
        logger.exception("Unexpected error in main loop")
    finally:
        logger.info("Shutting down...")
        stop_server()
        send_email_notification(
            "TinderGPT VPS Runner stopped",
            f"Server stopped at {datetime.now().isoformat()}",
        )
        logger.info("Goodbye!")


if __name__ == "__main__":
    main()
