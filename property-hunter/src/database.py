"""SQLite (local dev) / PostgreSQL (Railway) storage layer.

SQLite is used when DATABASE_URL env var is not set.
PostgreSQL is used when DATABASE_URL is set (Railway injects this automatically
when you add the PostgreSQL addon).
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import Property

DB_PATH = Path.home() / ".local" / "share" / "property-hunter" / "properties.db"
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_PG = bool(_DATABASE_URL)


@contextmanager
def _db():
    """Open a DB connection; commit on success, rollback on error, always close."""
    if _USE_PG:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(
            _DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _x(conn, sql: str, params=()):
    """Execute SQL, swapping ? → %s for PostgreSQL. Returns the cursor."""
    if _USE_PG:
        cur = conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur
    return conn.execute(sql, params)


def init_db() -> None:
    """Create tables if they don't exist yet."""
    with _db() as conn:
        _x(conn, """
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
        _x(conn, """
            CREATE TABLE IF NOT EXISTS alerts_sent (
                property_id TEXT,
                search_id   TEXT,
                sent_at     TEXT,
                PRIMARY KEY (property_id, search_id)
            )
        """)


def is_new(property_id: str, search_id: str) -> bool:
    """Return True if this property has NOT been alerted for this search before."""
    with _db() as conn:
        row = _x(conn,
            "SELECT 1 FROM alerts_sent WHERE property_id = ? AND search_id = ?",
            (property_id, search_id),
        ).fetchone()
        return row is None


def mark_sent(property_id: str, search_id: str) -> None:
    """Record that an alert was sent for this property + search pair."""
    with _db() as conn:
        if _USE_PG:
            _x(conn,
                "INSERT INTO alerts_sent (property_id, search_id, sent_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT (property_id, search_id) DO NOTHING",
                (property_id, search_id, datetime.now().isoformat()),
            )
        else:
            _x(conn,
                "INSERT OR IGNORE INTO alerts_sent VALUES (?, ?, ?)",
                (property_id, search_id, datetime.now().isoformat()),
            )


def save_property(prop: Property) -> None:
    """Upsert a property into the database."""
    params = (
        prop.id, prop.source, prop.listing_type, prop.url,
        prop.price, prop.bedrooms, prop.property_type, prop.address,
        prop.title, prop.postcode, prop.image_url,
        prop.epc_rating, prop.crime_rate, prop.commute_minutes,
        prop.first_seen.isoformat(),
    )
    with _db() as conn:
        if _USE_PG:
            _x(conn, """
                INSERT INTO properties
                    (id, source, listing_type, url, price, bedrooms, property_type,
                     address, title, postcode, image_url, epc_rating, crime_rate,
                     commute_minutes, first_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (id) DO UPDATE SET
                    source          = EXCLUDED.source,
                    listing_type    = EXCLUDED.listing_type,
                    url             = EXCLUDED.url,
                    price           = EXCLUDED.price,
                    bedrooms        = EXCLUDED.bedrooms,
                    property_type   = EXCLUDED.property_type,
                    address         = EXCLUDED.address,
                    title           = EXCLUDED.title,
                    postcode        = EXCLUDED.postcode,
                    image_url       = EXCLUDED.image_url,
                    epc_rating      = EXCLUDED.epc_rating,
                    crime_rate      = EXCLUDED.crime_rate,
                    commute_minutes = EXCLUDED.commute_minutes
            """, params)
        else:
            _x(conn, """
                INSERT OR REPLACE INTO properties
                    (id, source, listing_type, url, price, bedrooms, property_type,
                     address, title, postcode, image_url, epc_rating, crime_rate,
                     commute_minutes, first_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, params)


def recent_properties(limit: int = 20) -> list:
    """Return the most recently seen properties (for the 'recent' command)."""
    with _db() as conn:
        return _x(conn,
            "SELECT * FROM properties ORDER BY first_seen DESC LIMIT ?", (limit,)
        ).fetchall()


# ── Web UI extras ─────────────────────────────────────────────────────────────

def init_web_tables() -> None:
    """Create user_marks table for favourites/hidden (web UI only)."""
    with _db() as conn:
        _x(conn, """
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

    with _db() as conn:
        return _x(conn, f"""
            SELECT p.*,
                   COALESCE(u.favourited, 0) AS favourited,
                   COALESCE(u.hidden, 0)     AS hidden
            FROM properties p
            LEFT JOIN user_marks u ON p.id = u.property_id
            {where}
            ORDER BY p.first_seen DESC
            LIMIT ?
        """, params).fetchall()


def toggle_favourite(property_id: str) -> bool:
    """Toggle a property's favourite status. Returns the new state."""
    with _db() as conn:
        if _USE_PG:
            _x(conn,
                "INSERT INTO user_marks (property_id) VALUES (?)"
                " ON CONFLICT (property_id) DO NOTHING",
                (property_id,),
            )
        else:
            _x(conn,
                "INSERT OR IGNORE INTO user_marks (property_id) VALUES (?)",
                (property_id,),
            )
        _x(conn,
            "UPDATE user_marks SET favourited = 1 - favourited WHERE property_id = ?",
            (property_id,),
        )
        row = _x(conn,
            "SELECT favourited FROM user_marks WHERE property_id = ?",
            (property_id,),
        ).fetchone()
        return bool(row["favourited"]) if row else False


def hide_property(property_id: str) -> None:
    """Mark a property as hidden so it won't appear in the feed."""
    with _db() as conn:
        if _USE_PG:
            _x(conn,
                "INSERT INTO user_marks (property_id) VALUES (?)"
                " ON CONFLICT (property_id) DO NOTHING",
                (property_id,),
            )
        else:
            _x(conn,
                "INSERT OR IGNORE INTO user_marks (property_id) VALUES (?)",
                (property_id,),
            )
        _x(conn,
            "UPDATE user_marks SET hidden = 1 WHERE property_id = ?",
            (property_id,),
        )
