#!/usr/bin/env python3
"""
Signal Listener: web server + scheduled pipeline.

Serves the dashboard and runs the collection pipeline on a schedule.
Single service on Railway with one persistent volume.
"""

import os
import threading
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8080))
ROOT_DIR = Path(__file__).parent
DOCS_DIR = ROOT_DIR / "docs"
VOLUME_PATH = Path(os.environ.get("VOLUME_PATH", str(ROOT_DIR)))
DATA_DIR = VOLUME_PATH / "docs" / "data"

# Schedule: comma-separated weekday numbers (0=Mon, 1=Tue, ... 6=Sun)
COLLECT_DAYS = os.environ.get("COLLECT_DAYS", "1,4")  # Tue + Fri
COLLECT_HOUR = int(os.environ.get("COLLECT_HOUR", "6"))  # 6 AM UTC

log = logging.getLogger("signal-listener")


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serve dashboard HTML from app, JSON data from volume."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

    def do_GET(self):
        # Serve data/ requests from the volume instead of the app dir
        if self.path.startswith("/data/") and DATA_DIR.exists():
            file_path = DATA_DIR / self.path[6:]  # strip "/data/"
            if file_path.exists() and file_path.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-cache, must-revalidate")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
                return

        super().do_GET()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format, *args):
        # Quiet down request logging
        pass


def run_scheduler():
    """Simple scheduler that runs the pipeline on configured days."""
    import time
    from datetime import datetime, timezone

    collect_days = {int(d.strip()) for d in COLLECT_DAYS.split(",")}
    log.info(f"Scheduler started: running on weekdays {collect_days} at {COLLECT_HOUR}:00 UTC")

    last_run_date = None

    while True:
        now = datetime.now(timezone.utc)
        today = now.date()

        if (
            now.weekday() in collect_days
            and now.hour >= COLLECT_HOUR
            and last_run_date != today
        ):
            log.info(f"Scheduler: starting pipeline run for {today}")
            try:
                from scripts.pipeline import main as run_pipeline
                run_pipeline()
                last_run_date = today
                log.info(f"Scheduler: pipeline complete for {today}")
            except Exception as e:
                log.error(f"Scheduler: pipeline failed: {e}")
                last_run_date = today  # Don't retry today

        # Check every 15 minutes
        time.sleep(900)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Ensure data dir exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Start the scheduler in a background thread
    scheduler = threading.Thread(target=run_scheduler, daemon=True)
    scheduler.start()

    # Start the web server (main thread)
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    log.info(f"Dashboard running on port {PORT}")
    log.info(f"  HTML from: {DOCS_DIR}")
    log.info(f"  Data from: {DATA_DIR}")
    server.serve_forever()


if __name__ == "__main__":
    main()
