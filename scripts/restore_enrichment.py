#!/usr/bin/env python3
"""
Restore enrichment data (store URLs, emails) into the signals DB.

The 2026-04-07 Railway sync overwrote locally-enriched signal rows with
unenriched ones. The enrichment survived in git history as a dashboard
export; data/enrichment_backup_20260402.json is the slim extract
(signal_id -> {company_url, email}).

Fill-only semantics: never overwrites a non-empty value already in the DB.

Usage:
    python -m scripts.restore_enrichment            # restore into DB_PATH
    python -m scripts.restore_enrichment --dry-run  # report without writing
"""

import sys
import json
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection
from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("signal-listener")

DEFAULT_DATA = Path(__file__).parent.parent / "data" / "enrichment_backup_20260402.json"


def restore(data_path: Path, db_path: Path, dry_run: bool = False) -> dict:
    records = json.loads(Path(data_path).read_text())
    conn = get_connection(db_path)

    stats = {"matched": 0, "urls_restored": 0, "emails_restored": 0,
             "already_had_url": 0, "missing_ids": 0}

    for signal_id, rec in records.items():
        row = conn.execute(
            "SELECT author_company_url, raw_json FROM signals WHERE id = ?",
            (signal_id,),
        ).fetchone()
        if row is None:
            stats["missing_ids"] += 1
            continue
        stats["matched"] += 1

        raw = json.loads(row["raw_json"])
        raw.setdefault("author", {})
        raw.setdefault("metadata", {})
        changed = False

        url = rec.get("company_url", "")
        if url:
            if row["author_company_url"]:
                stats["already_had_url"] += 1
            else:
                raw["author"]["company_url"] = url
                if not dry_run:
                    conn.execute(
                        "UPDATE signals SET author_company_url = ? WHERE id = ?",
                        (url, signal_id),
                    )
                stats["urls_restored"] += 1
                changed = True

        email = rec.get("email", "")
        if email and not raw["metadata"].get("email"):
            raw["metadata"]["email"] = email
            stats["emails_restored"] += 1
            changed = True

        if changed and not dry_run:
            conn.execute(
                "UPDATE signals SET raw_json = ? WHERE id = ?",
                (json.dumps(raw), signal_id),
            )

    if not dry_run:
        conn.commit()
    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Restore enrichment from git-archived export")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log.info(f"Restoring from {args.data} into {args.db} (dry_run={args.dry_run})")
    stats = restore(args.data, args.db, dry_run=args.dry_run)
    for k, v in stats.items():
        log.info(f"  {k}: {v}")

    conn = get_connection(args.db)
    resolved = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE author_company_url IS NOT NULL AND author_company_url != ''"
    ).fetchone()[0]
    conn.close()
    log.info(f"  stores_resolved now: {resolved}")


if __name__ == "__main__":
    main()
