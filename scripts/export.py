#!/usr/bin/env python3
"""
Export signals from SQLite to JSON files for the dashboard.

Usage:
    python -m scripts.export           # Export all data
    python -m scripts.export --pretty  # Pretty-print JSON
"""

import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, get_signals, get_stats, get_connection
from config import DB_PATH, DASHBOARD_DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")


def export_all(pretty: bool = False):
    """Export all signals and stats to JSON for the dashboard."""
    init_db(DB_PATH)
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)

    indent = 2 if pretty else None

    # Export all signals
    signals = get_signals(limit=10000, db_path=DB_PATH)
    signals_path = DASHBOARD_DATA_DIR / "signals.json"
    with open(signals_path, "w") as f:
        json.dump(signals, f, indent=indent, ensure_ascii=False)
    log.info(f"Exported {len(signals)} signals to {signals_path}")

    # Export stats
    stats = get_stats(DB_PATH)
    stats_path = DASHBOARD_DATA_DIR / "stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=indent, ensure_ascii=False)
    log.info(f"Exported stats to {stats_path}")

    # Export by source (for backwards compat and lighter loading)
    conn = get_connection(DB_PATH)
    sources = conn.execute("SELECT DISTINCT source FROM signals").fetchall()
    conn.close()

    for row in sources:
        source = row["source"]
        source_signals = get_signals(source=source, limit=10000, db_path=DB_PATH)
        source_path = DASHBOARD_DATA_DIR / f"{source}.json"
        with open(source_path, "w") as f:
            json.dump(source_signals, f, indent=indent, ensure_ascii=False)
        log.info(f"  {source}: {len(source_signals)} signals -> {source_path}")

    # Export hot signals (separate file for alerts)
    hot = get_signals(urgency="hot", limit=10000, db_path=DB_PATH)
    hot_path = DASHBOARD_DATA_DIR / "hot_signals.json"
    with open(hot_path, "w") as f:
        json.dump(hot, f, indent=indent, ensure_ascii=False)
    log.info(f"Exported {len(hot)} hot signals to {hot_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Export DB to dashboard JSON")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    export_all(pretty=args.pretty)


if __name__ == "__main__":
    main()
