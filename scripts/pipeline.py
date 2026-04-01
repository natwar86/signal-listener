#!/usr/bin/env python3
"""
Full signal collection pipeline: collect → classify → export.

Used by Railway cron service. Runs all steps in sequence and writes
output JSON to the volume so the web service can serve it.
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, get_stats
from config import (
    DB_PATH, DASHBOARD_DATA_DIR, SHOPIFY_APPS,
    SHOPIFY_MIN_DELAY, SHOPIFY_MAX_DELAY, ANTHROPIC_API_KEY,
)
from collectors.base import PoliteFetcher
from collectors.shopify_reviews import collect_shopify_reviews
from processor.classifier import classify_signal
from scripts.export import export_all
from db import get_unclassified_signals, update_classification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")


def step_collect(max_pages=None, apps=None):
    """Collect reviews from all configured Shopify apps."""
    log.info("=" * 60)
    log.info("STEP 1: Collecting Shopify reviews")
    log.info("=" * 60)

    app_list = apps or SHOPIFY_APPS
    fetcher = PoliteFetcher(min_delay=SHOPIFY_MIN_DELAY, max_delay=SHOPIFY_MAX_DELAY)
    total_new = 0

    try:
        for app_slug in app_list:
            log.info(f"Collecting: {app_slug}")
            signals = collect_shopify_reviews(
                app_slug=app_slug,
                fetcher=fetcher,
                max_pages=max_pages,
                skip_resolve=True,  # Skip store URL resolution in automated runs
                db_path=DB_PATH,
            )
            total_new += len(signals)
    except Exception as e:
        log.error(f"Collection error: {e}")
    finally:
        fetcher.close()

    log.info(f"Collection complete. {total_new} new signals.")
    return total_new


def step_classify():
    """Classify any unclassified signals."""
    log.info("=" * 60)
    log.info("STEP 2: Classifying signals")
    log.info("=" * 60)

    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not set — skipping classification")
        return 0

    signals = get_unclassified_signals(limit=500, db_path=DB_PATH)
    log.info(f"Found {len(signals)} unclassified signals")

    if not signals:
        return 0

    classified = 0
    for i, signal in enumerate(signals, 1):
        log.info(f"[{i}/{len(signals)}] Classifying {signal['id']}...")
        result = classify_signal(signal)
        if result:
            update_classification(signal["id"], result, db_path=DB_PATH)
            classified += 1
        else:
            log.warning(f"  Failed to classify {signal['id']}")

    log.info(f"Classified {classified}/{len(signals)} signals.")
    return classified


def step_export():
    """Export DB to JSON for the dashboard."""
    log.info("=" * 60)
    log.info("STEP 3: Exporting to dashboard JSON")
    log.info("=" * 60)

    export_all(pretty=False)

    stats = get_stats(DB_PATH)
    log.info(f"Total signals: {stats['total_signals']}")
    log.info(f"Classified: {stats['classified']}")
    if stats.get("by_urgency"):
        log.info(f"Urgency: {stats['by_urgency']}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run the signal collection pipeline")
    parser.add_argument("--max-pages", type=int, help="Max pages per app (for testing)")
    parser.add_argument("--apps", nargs="*", help="Only collect from these app slugs")
    parser.add_argument("--skip-collect", action="store_true", help="Skip collection, only classify + export")
    parser.add_argument("--skip-classify", action="store_true", help="Skip classification")
    args = parser.parse_args()

    log.info("Signal Listener pipeline starting")
    init_db(DB_PATH)

    if not args.skip_collect:
        new = step_collect(max_pages=args.max_pages, apps=args.apps)
        if new > 0 and not args.skip_classify:
            step_classify()
        elif new == 0:
            log.info("No new signals — skipping classification")
    elif not args.skip_classify:
        step_classify()

    step_export()
    log.info("Pipeline complete.")


if __name__ == "__main__":
    main()
