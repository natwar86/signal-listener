#!/usr/bin/env python3
"""
Migrate V0.1 Shopify review data into the V2 signal database.

Usage:
    python -m scripts.migrate_v01 /path/to/saltbox-2/docs/data

This reads the JSON files (shipbob.json, shiphero.json, shipmonk.json)
from the V0.1 project and inserts them as signals into the SQLite DB.
"""

import sys
import json
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, bulk_insert_signals, get_stats
from processor.schema import Signal, Author, Content
from collectors.shopify_reviews import parse_date_to_iso
from config import DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")

BASE_URL = "https://apps.shopify.com"


def migrate_review(review: dict, app_slug: str) -> Signal:
    """Convert a V0.1 review dict to a V2 Signal."""
    review_id = review.get("review_id", "")
    signal_id = f"shopify_{app_slug}_{review_id}" if review_id else ""

    review_link = review.get("review_link", "")
    source_url = f"{BASE_URL}{review_link}" if review_link else ""

    return Signal(
        id=signal_id,
        source="shopify_reviews",
        source_url=source_url,
        timestamp=parse_date_to_iso(review.get("date", "")),
        author=Author(
            name=review.get("reviewer"),
            company=review.get("reviewer"),
            company_url=review.get("store_url", ""),
        ),
        content=Content(
            body=review.get("body", ""),
            rating=review.get("rating"),
        ),
        metadata={
            "app_slug": app_slug,
            "app_url": review.get("app_url", f"{BASE_URL}/{app_slug}"),
            "location": review.get("location", ""),
            "usage_duration": review.get("usage_duration", ""),
            "review_link": review_link,
        },
    )


def main():
    if len(sys.argv) < 2:
        # Default: look for data in the sibling saltbox-2 repo
        data_dir = Path(__file__).parent.parent.parent / "saltbox-2" / "docs" / "data"
    else:
        data_dir = Path(sys.argv[1])

    if not data_dir.exists():
        log.error(f"Data directory not found: {data_dir}")
        log.info("Usage: python -m scripts.migrate_v01 /path/to/saltbox-2/docs/data")
        sys.exit(1)

    init_db(DB_PATH)

    total_inserted = 0
    json_files = sorted(data_dir.glob("*.json"))

    if not json_files:
        log.error(f"No JSON files found in {data_dir}")
        sys.exit(1)

    for json_file in json_files:
        app_slug = json_file.stem
        log.info(f"Migrating {app_slug} from {json_file}...")

        with open(json_file) as f:
            reviews = json.load(f)

        signals = [migrate_review(r, app_slug) for r in reviews]
        inserted = bulk_insert_signals(signals, DB_PATH)
        total_inserted += inserted
        log.info(f"  {app_slug}: {len(reviews)} reviews -> {inserted} new signals")

    stats = get_stats(DB_PATH)
    log.info(f"Migration complete. DB stats:")
    log.info(f"  Total signals: {stats['total_signals']}")
    log.info(f"  By source: {stats['by_source']}")
    log.info(f"  Stores resolved: {stats['stores_resolved']}")
    log.info(f"  Unclassified: {stats['unclassified']}")


if __name__ == "__main__":
    main()
