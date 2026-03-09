#!/usr/bin/env python3
"""
Run AI classification on unclassified signals.

Usage:
    python -m scripts.classify              # Classify up to 100 signals
    python -m scripts.classify --limit 500  # Classify more
    python -m scripts.classify --dry-run    # Preview without calling API
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import init_db, get_unclassified_signals, update_classification, get_stats
from processor.classifier import classify_signal
from config import DB_PATH, ANTHROPIC_API_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal-listener")


def main():
    parser = argparse.ArgumentParser(description="Classify unclassified signals")
    parser.add_argument("--limit", type=int, default=100, help="Max signals to classify")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be classified")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not ANTHROPIC_API_KEY and not args.dry_run:
        log.error("ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    init_db(DB_PATH)
    signals = get_unclassified_signals(limit=args.limit, db_path=DB_PATH)
    log.info(f"Found {len(signals)} unclassified signals")

    if not signals:
        log.info("Nothing to classify.")
        return

    if args.dry_run:
        for s in signals[:10]:
            rating = s.get("content_rating", "?")
            body = (s.get("content_body", "") or "")[:80]
            log.info(f"  [{s['source']}] rating={rating} | {body}...")
        if len(signals) > 10:
            log.info(f"  ... and {len(signals) - 10} more")
        return

    classified = 0
    failed = 0
    for i, signal in enumerate(signals, 1):
        log.info(f"[{i}/{len(signals)}] Classifying {signal['id']}...")
        result = classify_signal(signal)
        if result:
            update_classification(signal["id"], result, db_path=DB_PATH)
            urgency = result.get("urgency", "?")
            sentiment = result.get("sentiment", "?")
            summary = result.get("summary", "")[:60]
            log.info(f"  -> {urgency}/{sentiment}: {summary}")
            classified += 1
        else:
            log.warning(f"  -> Failed to classify")
            failed += 1

    log.info(f"Classification complete: {classified} classified, {failed} failed")

    stats = get_stats(DB_PATH)
    log.info(f"DB stats: {stats['classified']}/{stats['total_signals']} classified")
    if stats.get("by_urgency"):
        log.info(f"  Urgency: {stats['by_urgency']}")
    if stats.get("by_sentiment"):
        log.info(f"  Sentiment: {stats['by_sentiment']}")


if __name__ == "__main__":
    main()
