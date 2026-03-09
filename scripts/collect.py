#!/usr/bin/env python3
"""
Run data collectors.

Usage:
    python -m scripts.collect                          # Run all collectors
    python -m scripts.collect shopify                  # Shopify only
    python -m scripts.collect shopify --apps shipbob   # Single app
    python -m scripts.collect shopify --max-pages 3    # Test mode
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db
from config import DB_PATH, SHOPIFY_APPS, SHOPIFY_MIN_DELAY, SHOPIFY_MAX_DELAY
from collectors.base import PoliteFetcher
from collectors.shopify_reviews import collect_shopify_reviews

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")


def run_shopify(args):
    apps = args.apps if args.apps else SHOPIFY_APPS
    min_delay = args.min_delay or SHOPIFY_MIN_DELAY
    max_delay = args.max_delay or SHOPIFY_MAX_DELAY

    fetcher = PoliteFetcher(min_delay=min_delay, max_delay=max_delay)
    total_new = 0

    try:
        for app_slug in apps:
            signals = collect_shopify_reviews(
                app_slug=app_slug,
                fetcher=fetcher,
                max_pages=args.max_pages,
                save_html=args.save_html,
                skip_resolve=args.skip_resolve,
                db_path=DB_PATH,
            )
            total_new += len(signals)
    except KeyboardInterrupt:
        log.info("\nInterrupted — progress saved. Run again to resume.")
    finally:
        fetcher.close()

    log.info(f"Shopify collection complete. {total_new} new signals total.")


def main():
    parser = argparse.ArgumentParser(description="Run signal collectors")
    parser.add_argument("source", nargs="?", default="all",
                        choices=["all", "shopify", "reddit"],
                        help="Which collector to run")
    parser.add_argument("--apps", nargs="*", help="Specific app slugs (shopify only)")
    parser.add_argument("--max-pages", type=int, help="Max pages per app (testing)")
    parser.add_argument("--min-delay", type=float, help="Min delay between requests")
    parser.add_argument("--max-delay", type=float, help="Max delay between requests")
    parser.add_argument("--save-html", action="store_true", help="Save raw HTML for debugging")
    parser.add_argument("--skip-resolve", action="store_true", help="Skip store URL resolution")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    init_db(DB_PATH)

    if args.source in ("all", "shopify"):
        run_shopify(args)

    if args.source in ("all", "reddit"):
        log.info("Reddit collector not yet implemented — skipping")


if __name__ == "__main__":
    main()
