import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .models import Property

DB_PATH = Path.home() / ".local" / "share" / "property-alert" / "properties.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS properties (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                listing_type TEXT NOT NULL,
                url TEXT,
                title TEXT,
                price INTEGER,
                price_frequency TEXT,
                bedrooms INTEGER,
                bathrooms INTEGER,
                property_type TEXT,
                address TEXT,
                postcode TEXT,
                latitude REAL,
                longitude REAL,
                description TEXT,
                features TEXT,
                images TEXT,
                agent_name TEXT,
                listed_date TEXT,
                first_seen TEXT,
                last_seen TEXT,
                status TEXT DEFAULT 'new',
                crime_score TEXT,
                crime_summary TEXT,
                epc_rating TEXT,
                avg_sold_price INTEGER,
                commute_minutes INTEGER,
                nearest_school TEXT,
                nearest_school_rating TEXT
            );

            CREATE TABLE IF NOT EXISTS notifications_sent (
                property_id TEXT,
                search_id TEXT,
                sent_at TEXT,
                PRIMARY KEY (property_id, search_id)
            );
        """)


def save_property(prop: Property) -> bool:
    """Upsert a property. Returns True if it is brand new."""
    now = datetime.now(timezone.utc).isoformat()
    d = prop.to_dict()
    d["last_seen"] = now

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM properties WHERE id = ?", (prop.id,)
        ).fetchone()

        if existing:
            # Update last_seen and any enrichment fields that were populated
            conn.execute(
                """UPDATE properties SET last_seen=:last_seen,
                   crime_score=COALESCE(:crime_score, crime_score),
                   crime_summary=COALESCE(:crime_summary, crime_summary),
                   epc_rating=COALESCE(:epc_rating, epc_rating),
                   avg_sold_price=COALESCE(:avg_sold_price, avg_sold_price),
                   commute_minutes=COALESCE(:commute_minutes, commute_minutes),
                   nearest_school=COALESCE(:nearest_school, nearest_school),
                   nearest_school_rating=COALESCE(:nearest_school_rating, nearest_school_rating)
                   WHERE id=:id""",
                d,
            )
            return False

        d["first_seen"] = now
        cols = ", ".join(d.keys())
        placeholders = ", ".join(f":{k}" for k in d.keys())
        conn.execute(f"INSERT INTO properties ({cols}) VALUES ({placeholders})", d)
        return True


def is_notified(property_id: str, search_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notifications_sent WHERE property_id=? AND search_id=?",
            (property_id, search_id),
        ).fetchone()
        return row is not None


def mark_notified(property_id: str, search_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notifications_sent VALUES (?, ?, ?)",
            (property_id, search_id, now),
        )


def get_recent(limit: int = 20) -> list[Property]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM properties ORDER BY first_seen DESC LIMIT ?", (limit,)
        ).fetchall()
    return [Property.from_row(dict(r)) for r in rows]


def get_stats() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
        notified = conn.execute("SELECT COUNT(DISTINCT property_id) FROM notifications_sent").fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) as n FROM properties GROUP BY source"
        ).fetchall()
    return {
        "total_properties": total,
        "total_notified": notified,
        "by_source": {r["source"]: r["n"] for r in by_source},
    }
