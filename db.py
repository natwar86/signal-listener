"""
SQLite database layer for signal storage.

Single file DB — no server, no infrastructure. Signals are stored as JSON
blobs alongside indexed columns for fast filtering.
"""

import re
import json
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional

from processor.schema import Signal

DEFAULT_DB_PATH = Path(__file__).parent / "signals.db"

# Reviewer names that are store-generic placeholders, not real companies
GENERIC_COMPANY_NAMES = {"my store", "my shop", "online store", "shopify store"}


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH):
    """Create tables if they don't exist."""
    conn = get_connection(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_url TEXT,
            timestamp TEXT,
            collected_at TEXT,

            -- Author (denormalized for query speed)
            author_name TEXT,
            author_company TEXT,
            author_company_url TEXT,

            -- Content
            content_title TEXT,
            content_body TEXT,
            content_rating REAL,

            -- Classification
            sentiment TEXT,
            urgency TEXT,
            pain_types TEXT,           -- JSON array
            competitors_mentioned TEXT, -- JSON array
            market TEXT,
            intent TEXT,
            summary TEXT,

            -- Full signal as JSON (source of truth)
            raw_json TEXT NOT NULL,

            -- Housekeeping
            classified_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source);
        CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
        CREATE INDEX IF NOT EXISTS idx_signals_urgency ON signals(urgency);
        CREATE INDEX IF NOT EXISTS idx_signals_sentiment ON signals(sentiment);
        CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(market);
        CREATE INDEX IF NOT EXISTS idx_signals_rating ON signals(content_rating);
        CREATE INDEX IF NOT EXISTS idx_signals_classified ON signals(classified_at);

        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,                 -- display name as seen in reviews
            normalized_name TEXT NOT NULL UNIQUE,
            domain TEXT,
            url TEXT,
            email TEXT,
            contact_json TEXT,                  -- decision-maker contact (Apollo etc.)
            resolution_source TEXT,             -- myshopify | exa | domain | restored_apr2 | manual
            resolution_confidence TEXT,         -- verified | likely | unverified | rejected
            verified_at TEXT,
            enriched_at TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
    """)
    # company_id lands via ALTER because the signals table predates it
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
    if "company_id" not in cols:
        conn.execute("ALTER TABLE signals ADD COLUMN company_id INTEGER REFERENCES companies(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_company ON signals(company_id)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

def normalize_company_name(name: str) -> str:
    """Canonical key for a company: ascii, lowercase, no punctuation."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def get_or_create_company(name: str, db_path: Path = DEFAULT_DB_PATH,
                          conn: Optional[sqlite3.Connection] = None) -> Optional[int]:
    """Return company id for a display name, creating the row if new.
    Returns None for empty/generic names (e.g. 'My Store')."""
    norm = normalize_company_name(name)
    if not norm or norm in GENERIC_COMPANY_NAMES:
        return None

    own_conn = conn is None
    if own_conn:
        conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM companies WHERE normalized_name = ?", (norm,)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO companies (name, normalized_name) VALUES (?, ?)",
            (name, norm),
        )
        if own_conn:
            conn.commit()
        return cur.lastrowid
    finally:
        if own_conn:
            conn.close()


def update_company(company_id: int, fields: dict, db_path: Path = DEFAULT_DB_PATH,
                   conn: Optional[sqlite3.Connection] = None):
    """Update company columns from a dict of {column: value}."""
    allowed = {"domain", "url", "email", "contact_json", "resolution_source",
               "resolution_confidence", "verified_at", "enriched_at", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_connection(db_path)
    try:
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE companies SET {sets}, updated_at = datetime('now') WHERE id = ?",
            (*updates.values(), company_id),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def get_company_map(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """All companies keyed by id, for export joins."""
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM companies").fetchall()
    conn.close()
    return {r["id"]: dict(r) for r in rows}


def insert_signal(signal: Signal, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Insert a signal. Returns True if inserted, False if duplicate."""
    conn = get_connection(db_path)
    d = signal.to_dict()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO signals (
                id, source, source_url, timestamp, collected_at,
                author_name, author_company, author_company_url,
                content_title, content_body, content_rating,
                sentiment, urgency, pain_types, competitors_mentioned,
                market, intent, summary,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.id, signal.source, signal.source_url,
            signal.timestamp, signal.collected_at,
            signal.author.name, signal.author.company, signal.author.company_url,
            signal.content.title, signal.content.body, signal.content.rating,
            signal.classification.sentiment, signal.classification.urgency,
            json.dumps(signal.classification.pain_types),
            json.dumps(signal.classification.competitors_mentioned),
            signal.classification.market, signal.classification.intent,
            signal.classification.summary,
            json.dumps(d),
        ))
        inserted = conn.total_changes > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def bulk_insert_signals(signals: list[Signal], db_path: Path = DEFAULT_DB_PATH) -> int:
    """Insert multiple signals. Returns count of newly inserted."""
    conn = get_connection(db_path)
    inserted = 0
    for signal in signals:
        d = signal.to_dict()
        try:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO signals (
                    id, source, source_url, timestamp, collected_at,
                    author_name, author_company, author_company_url,
                    content_title, content_body, content_rating,
                    sentiment, urgency, pain_types, competitors_mentioned,
                    market, intent, summary,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.id, signal.source, signal.source_url,
                signal.timestamp, signal.collected_at,
                signal.author.name, signal.author.company, signal.author.company_url,
                signal.content.title, signal.content.body, signal.content.rating,
                signal.classification.sentiment, signal.classification.urgency,
                json.dumps(signal.classification.pain_types),
                json.dumps(signal.classification.competitors_mentioned),
                signal.classification.market, signal.classification.intent,
                signal.classification.summary,
                json.dumps(d),
            ))
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return inserted


def update_classification(signal_id: str, classification: dict, db_path: Path = DEFAULT_DB_PATH):
    """Update the classification fields for a signal after AI processing."""
    conn = get_connection(db_path)

    # Update the indexed columns
    conn.execute("""
        UPDATE signals SET
            sentiment = ?,
            urgency = ?,
            pain_types = ?,
            competitors_mentioned = ?,
            market = ?,
            intent = ?,
            summary = ?,
            classified_at = datetime('now')
        WHERE id = ?
    """, (
        classification.get("sentiment"),
        classification.get("urgency"),
        json.dumps(classification.get("pain_types", [])),
        json.dumps(classification.get("competitors_mentioned", [])),
        classification.get("market"),
        classification.get("intent"),
        classification.get("summary"),
        signal_id,
    ))

    # Also update the raw_json blob
    row = conn.execute("SELECT raw_json FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if row:
        d = json.loads(row["raw_json"])
        d["classification"] = classification
        conn.execute("UPDATE signals SET raw_json = ? WHERE id = ?",
                     (json.dumps(d), signal_id))

    conn.commit()
    conn.close()


def get_unclassified_signals(limit: int = 100, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Get signals that haven't been classified yet."""
    conn = get_connection(db_path)
    rows = conn.execute("""
        SELECT id, source, content_body, content_rating, author_name, author_company,
               raw_json
        FROM signals
        WHERE classified_at IS NULL AND content_body != ''
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signals(
    source: Optional[str] = None,
    urgency: Optional[str] = None,
    sentiment: Optional[str] = None,
    market: Optional[str] = None,
    min_rating: Optional[float] = None,
    max_rating: Optional[float] = None,
    limit: int = 500,
    offset: int = 0,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Query signals with optional filters."""
    conn = get_connection(db_path)
    clauses = []
    params = []

    if source:
        clauses.append("source = ?")
        params.append(source)
    if urgency:
        clauses.append("urgency = ?")
        params.append(urgency)
    if sentiment:
        clauses.append("sentiment = ?")
        params.append(sentiment)
    if market:
        clauses.append("market = ?")
        params.append(market)
    if min_rating is not None:
        clauses.append("content_rating >= ?")
        params.append(min_rating)
    if max_rating is not None:
        clauses.append("content_rating <= ?")
        params.append(max_rating)

    where = " AND ".join(clauses) if clauses else "1=1"
    params.extend([limit, offset])

    rows = conn.execute(f"""
        SELECT raw_json, company_id FROM signals
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """, params).fetchall()
    conn.close()

    signals = []
    for r in rows:
        d = json.loads(r["raw_json"])
        d["company_id"] = r["company_id"]
        signals.append(d)
    return signals


def get_stats(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Get aggregate stats for the dashboard."""
    conn = get_connection(db_path)
    stats = {}

    stats["total_signals"] = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    stats["classified"] = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE classified_at IS NOT NULL"
    ).fetchone()[0]
    stats["unclassified"] = stats["total_signals"] - stats["classified"]

    # By source
    rows = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM signals GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    stats["by_source"] = {r["source"]: r["cnt"] for r in rows}

    # By urgency (classified only)
    rows = conn.execute(
        "SELECT urgency, COUNT(*) as cnt FROM signals WHERE urgency IS NOT NULL GROUP BY urgency"
    ).fetchall()
    stats["by_urgency"] = {r["urgency"]: r["cnt"] for r in rows}

    # By sentiment
    rows = conn.execute(
        "SELECT sentiment, COUNT(*) as cnt FROM signals WHERE sentiment IS NOT NULL GROUP BY sentiment"
    ).fetchall()
    stats["by_sentiment"] = {r["sentiment"]: r["cnt"] for r in rows}

    # Avg rating (for review sources)
    row = conn.execute(
        "SELECT AVG(content_rating) FROM signals WHERE content_rating IS NOT NULL"
    ).fetchone()
    stats["avg_rating"] = round(row[0], 2) if row[0] else None

    # Stores resolved
    stats["stores_resolved"] = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE author_company_url IS NOT NULL AND author_company_url != ''"
    ).fetchone()[0]

    # Companies (deduped stores)
    stats["companies_total"] = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    stats["companies_resolved"] = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE url IS NOT NULL AND url != ''"
    ).fetchone()[0]
    stats["companies_with_email"] = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE email IS NOT NULL AND email != ''"
    ).fetchone()[0]

    conn.close()
    return stats
