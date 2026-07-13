#!/usr/bin/env python3
"""
Populate the companies table from existing signals and link signals to it.

Shopify app-store reviewer names ARE store/business names, so each distinct
reviewer becomes a company row, deduped across apps by normalized name.
Google Maps reviewers are people, not companies — those signals stay unlinked.

Enrichment (url/email) found on signal rows is folded into the company:
first non-empty value wins; disagreements are logged and noted on the row.

Idempotent — safe to re-run.

Usage:
    python -m scripts.migrate_companies
    python -m scripts.migrate_companies --dry-run
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import (
    get_connection, init_db, get_or_create_company, normalize_company_name,
    GENERIC_COMPANY_NAMES,
)
from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("signal-listener")


def domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def migrate(db_path: Path, dry_run: bool = False) -> dict:
    init_db(db_path)
    conn = get_connection(db_path)

    rows = conn.execute("""
        SELECT id, author_name, author_company_url, raw_json, company_id
        FROM signals
        WHERE source = 'shopify_reviews'
    """).fetchall()

    stats = {"signals_seen": len(rows), "signals_linked": 0, "companies_created": 0,
             "urls_folded": 0, "emails_folded": 0, "url_conflicts": 0,
             "skipped_generic": 0}
    before = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    for row in rows:
        name = (row["author_name"] or "").strip()
        norm = normalize_company_name(name)
        if not norm or norm in GENERIC_COMPANY_NAMES:
            stats["skipped_generic"] += 1
            continue

        company_id = get_or_create_company(name, conn=conn)
        if company_id is None:
            stats["skipped_generic"] += 1
            continue

        if row["company_id"] != company_id:
            if not dry_run:
                conn.execute("UPDATE signals SET company_id = ? WHERE id = ?",
                             (company_id, row["id"]))
            stats["signals_linked"] += 1

        # Fold enrichment from the signal into the company (first wins)
        url = (row["author_company_url"] or "").strip()
        email = ""
        try:
            email = (json.loads(row["raw_json"]).get("metadata") or {}).get("email", "") or ""
        except (json.JSONDecodeError, AttributeError):
            pass

        company = conn.execute("SELECT url, email, notes FROM companies WHERE id = ?",
                               (company_id,)).fetchone()
        if url:
            if not company["url"]:
                if not dry_run:
                    conn.execute("""
                        UPDATE companies SET url = ?, domain = ?,
                            resolution_source = 'restored_apr2',
                            resolution_confidence = 'unverified',
                            enriched_at = datetime('now'),
                            updated_at = datetime('now')
                        WHERE id = ?
                    """, (url, domain_of(url), company_id))
                stats["urls_folded"] += 1
            elif company["url"].rstrip("/") != url.rstrip("/"):
                stats["url_conflicts"] += 1
                note = f"url conflict: signal {row['id']} had {url}"
                log.warning(f"  {name}: {note} (kept {company['url']})")
                if not dry_run:
                    existing = company["notes"] or ""
                    conn.execute(
                        "UPDATE companies SET notes = ? WHERE id = ?",
                        ((existing + "; " if existing else "") + note, company_id),
                    )
        if email and not company["email"]:
            if not dry_run:
                conn.execute(
                    "UPDATE companies SET email = ?, updated_at = datetime('now') WHERE id = ?",
                    (email, company_id),
                )
            stats["emails_folded"] += 1

    after = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    stats["companies_created"] = after - before
    stats["companies_total"] = after

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Populate companies table from signals")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log.info(f"Migrating companies in {args.db} (dry_run={args.dry_run})")
    stats = migrate(args.db, dry_run=args.dry_run)
    for k, v in stats.items():
        log.info(f"  {k}: {v}")


if __name__ == "__main__":
    main()
