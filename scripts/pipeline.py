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
    DB_PATH, DASHBOARD_DATA_DIR, SHOPIFY_APPS, TRUSTPILOT_COMPANIES,
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
    """Collect reviews from all configured Shopify apps, then Trustpilot."""
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

    log.info("Collecting Trustpilot reviews")
    try:
        from collectors.trustpilot import collect_trustpilot_reviews, has_existing_signals
        # First run backfills everything; cron deltas only fetch last 30 days
        delta = has_existing_signals(DB_PATH)
        # Backfill ceiling is sized to the pessimistic pre-run estimate
        # (6 brands x 3000 reviews ~= $10); actual spend is bounded by real
        # review counts (~3.5k total ~= $2) and by Apify's own
        # maxTotalChargeUsd, so the ceiling never gets close to charging.
        # Backfill uses an explicit large cap: the actor silently caps at 200
        # per brand when maxReviewsPerCompany=0, despite its schema saying
        # 0 means unlimited (observed 2026-07-19).
        signals = collect_trustpilot_reviews(
            TRUSTPILOT_COMPANIES,
            max_reviews_per_company=200 if delta else 1500,
            date_preset="last30days" if delta else "",
            dry_run=False,
            max_cost_usd=1.00 if delta else 12.00,
            db_path=DB_PATH,
        )
        total_new += len(signals)
    except Exception as e:
        log.error(f"Trustpilot collection error: {e}")

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


def step_enrich(limit: int = 100):
    """Link new signals to companies and resolve new companies' websites.

    Incremental by design: migrate_companies is idempotent and fast, and
    get_companies_to_resolve skips anything already attempted — so a cron
    run only touches companies created since the last run.
    """
    log.info("=" * 60)
    log.info("STEP 3: Enriching new companies")
    log.info("=" * 60)

    from scripts.migrate_companies import migrate
    stats = migrate(DB_PATH)
    log.info(f"Linked {stats['signals_linked']} signals, "
             f"{stats['companies_created']} new companies")

    from scripts.enrich import get_companies_to_resolve, process_company, _run_pool
    companies = get_companies_to_resolve(limit)
    if not companies:
        log.info("No unresolved companies — skipping")
        return 0

    log.info(f"Resolving {len(companies)} companies")
    results = _run_pool(companies, process_company, workers=2)
    resolved = sum(1 for r in results if r["url"])
    log.info(f"Enrichment complete. Resolved {resolved}/{len(companies)}.")
    return resolved


def step_export():
    """Export DB to JSON for the dashboard."""
    log.info("=" * 60)
    log.info("STEP 4: Exporting to dashboard JSON")
    log.info("=" * 60)

    export_all(pretty=False)

    stats = get_stats(DB_PATH)
    log.info(f"Total signals: {stats['total_signals']}")
    log.info(f"Classified: {stats['classified']}")
    if stats.get("by_urgency"):
        log.info(f"Urgency: {stats['by_urgency']}")


def write_heartbeat(run_report: dict):
    """Persist a machine-readable record of this run so staleness and silent
    scraper breakage are visible on the dashboard (served at /data/last_run.json)."""
    import json
    from datetime import datetime, timezone

    run_report["finished_at"] = datetime.now(timezone.utc).isoformat()
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DASHBOARD_DATA_DIR / "last_run.json"
    path.write_text(json.dumps(run_report, indent=2))
    log.info(f"Heartbeat written to {path}")


def main():
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description="Run the signal collection pipeline")
    parser.add_argument("--max-pages", type=int, help="Max pages per app (for testing)")
    parser.add_argument("--apps", nargs="*", help="Only collect from these app slugs")
    parser.add_argument("--skip-collect", action="store_true", help="Skip collection, only classify + export")
    parser.add_argument("--skip-classify", action="store_true", help="Skip classification")
    parser.add_argument("--skip-enrich", action="store_true", help="Skip company enrichment")
    args = parser.parse_args()

    log.info("Signal Listener pipeline starting")
    init_db(DB_PATH)

    report = {"started_at": datetime.now(timezone.utc).isoformat(),
              "collected": None, "classified": None, "enriched": None,
              "errors": []}

    try:
        if not args.skip_collect:
            report["collected"] = step_collect(max_pages=args.max_pages, apps=args.apps)
            if report["collected"] > 0 and not args.skip_classify:
                report["classified"] = step_classify()
            elif report["collected"] == 0:
                log.info("No new signals — skipping classification")
        elif not args.skip_classify:
            report["classified"] = step_classify()

        if not args.skip_enrich:
            try:
                report["enriched"] = step_enrich()
            except Exception as e:
                log.error(f"Enrichment failed (continuing to export): {e}")
                report["errors"].append(f"enrich: {e}")

        step_export()

        stats = get_stats(DB_PATH)
        report["total_signals"] = stats["total_signals"]
        report["companies_resolved"] = stats.get("companies_resolved")
        report["status"] = "ok" if not report["errors"] else "partial"
    except Exception as e:
        log.error(f"Pipeline failed: {e}")
        report["errors"].append(str(e))
        report["status"] = "failed"
        raise
    finally:
        write_heartbeat(report)

    log.info("Pipeline complete.")


if __name__ == "__main__":
    main()
