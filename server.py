#!/usr/bin/env python3
"""
Simple web server for the Signal Listener dashboard.

Serves the static dashboard HTML from the app, and JSON data files
from the persistent volume (where the pipeline writes them).
"""

import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8080))
ROOT_DIR = Path(__file__).parent
DOCS_DIR = ROOT_DIR / "docs"
VOLUME_PATH = Path(os.environ.get("VOLUME_PATH", str(ROOT_DIR)))
DATA_DIR = VOLUME_PATH / "docs" / "data"


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


def main():
    # Ensure data dir exists (pipeline may not have run yet)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Dashboard running on port {PORT}")
    print(f"  HTML from: {DOCS_DIR}")
    print(f"  Data from: {DATA_DIR}")
    server.serve_forever()


if __name__ == "__main__":
    main()
