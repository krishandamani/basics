"""SQLite storage — tracks seen properties so you're never alerted twice."""

import sqlite3
from pathlib import Path
from datetime import datetime
from .models import Property

DB_PATH = Path.home() / ".local" / "share" / "property-hunter" / "properties.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    """Create tables if they don't exist yet."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS properties (
                id              TEXT PRIMARY KEY,
                source          TEXT,
                listing_type    TEXT,
                url             TEXT,
                price           INTEGER,
                bedrooms        INTEGER,
                property_type   TEXT,
                address         TEXT,
                title           TEXT,
                postcode        TEXT,
                image_url       TEXT,
                epc_rating      TEXT,
                crime_rate      TEXT,
                commute_minutes INTEGER,
                first_seen      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts_sent (
                property_id TEXT,
                search_id   TEXT,
                sent_at     TEXT,
                PRIMARY KEY (property_id, search_id)
            )
        """)


def is_new(property_id: str, search_id: str) -> bool:
    """Return True if this property has NOT been alerted for this search before."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM alerts_sent WHERE property_id = ? AND search_id = ?",
            (property_id, search_id),
        ).fetchone()
        return row is None


def mark_sent(property_id: str, search_id: str) -> None:
    """Record that an alert was sent for this property + search pair."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO alerts_sent VALUES (?, ?, ?)",
            (property_id, search_id, datetime.now().isoformat()),
        )


def save_property(prop: Property) -> None:
    """Upsert a property into the database."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO properties
                (id, source, listing_type, url, price, bedrooms, property_type,
                 address, title, postcode, image_url, epc_rating, crime_rate,
                 commute_minutes, first_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                prop.id, prop.source, prop.listing_type, prop.url,
                prop.price, prop.bedrooms, prop.property_type, prop.address,
                prop.title, prop.postcode, prop.image_url,
                prop.epc_rating, prop.crime_rate, prop.commute_minutes,
                prop.first_seen.isoformat(),
            ),
        )


def recent_properties(limit: int = 20) -> list:
    """Return the most recently seen properties (for the 'recent' command)."""
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM properties ORDER BY first_seen DESC LIMIT ?", (limit,)
        ).fetchall()


# ── Web UI extras ─────────────────────────────────────────────────────────────

def init_web_tables() -> None:
    """Create user_marks table for favourites/hidden (web UI only)."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_marks (
                property_id TEXT PRIMARY KEY,
                favourited  INTEGER DEFAULT 0,
                hidden      INTEGER DEFAULT 0
            )
        """)


def get_web_properties(
    listing_type: str = "",
    min_price: int = None,
    max_price: int = None,
    min_bedrooms: int = None,
    source: str = "",
    favourites_only: bool = False,
    limit: int = 300,
) -> list:
    """Return properties joined with user marks, for the web UI."""
    conditions = ["COALESCE(u.hidden, 0) = 0"]
    params: list = []

    if favourites_only:
        conditions.append("COALESCE(u.favourited, 0) = 1")
    if listing_type:
        conditions.append("p.listing_type = ?")
        params.append(listing_type)
    if min_price is not None:
        conditions.append("p.price >= ?")
        params.append(min_price)
    if max_price is not None:
        conditions.append("p.price <= ?")
        params.append(max_price)
    if min_bedrooms is not None:
        conditions.append("p.bedrooms >= ?")
        params.append(min_bedrooms)
    if source:
        conditions.append("p.source = ?")
        params.append(source)

    where = "WHERE " + " AND ".join(conditions)
    params.append(limit)

    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            f"""
            SELECT p.*,
                   COALESCE(u.favourited, 0) AS favourited,
                   COALESCE(u.hidden, 0)     AS hidden
            FROM properties p
            LEFT JOIN user_marks u ON p.id = u.property_id
            {where}
            ORDER BY p.first_seen DESC
            LIMIT ?
            """,
            params,
        ).fetchall()


def toggle_favourite(property_id: str) -> bool:
    """Toggle a property's favourite status. Returns the new state."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_marks (property_id) VALUES (?)", (property_id,)
        )
        conn.execute(
            "UPDATE user_marks SET favourited = 1 - favourited WHERE property_id = ?",
            (property_id,),
        )
        row = conn.execute(
            "SELECT favourited FROM user_marks WHERE property_id = ?", (property_id,)
        ).fetchone()
        return bool(row[0]) if row else False


def hide_property(property_id: str) -> None:
    """Mark a property as hidden so it won't appear in the feed."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_marks (property_id) VALUES (?)", (property_id,)
        )
        conn.execute(
            "UPDATE user_marks SET hidden = 1 WHERE property_id = ?", (property_id,)
        )
