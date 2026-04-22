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
from typing import Optional

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
                previous_price  INTEGER,
                bedrooms        INTEGER,
                property_type   TEXT,
                address         TEXT,
                title           TEXT,
                postcode        TEXT,
                image_url       TEXT,
                epc_rating      TEXT,
                crime_rate      TEXT,
                commute_minutes INTEGER,
                nearest_school  TEXT,
                school_rating   TEXT,
                agent_name            TEXT,
                nearest_station       TEXT,
                station_distance_miles REAL,
                first_seen            TEXT
            )
        """)
        # Add columns that may be missing from older schemas (safe to run repeatedly)
        for col, defn in [
            ("nearest_school", "TEXT"),
            ("school_rating", "TEXT"),
            ("previous_price", "INTEGER"),
            ("agent_name", "TEXT"),
            ("nearest_station", "TEXT"),
            ("station_distance_miles", "REAL"),
        ]:
            try:
                _x(conn, f"ALTER TABLE properties ADD COLUMN {col} {defn}")
            except Exception:
                pass  # already exists
        _x(conn, """
            CREATE TABLE IF NOT EXISTS alerts_sent (
                property_id TEXT,
                search_id   TEXT,
                sent_at     TEXT,
                PRIMARY KEY (property_id, search_id)
            )
        """)
        _x(conn, """
            CREATE TABLE IF NOT EXISTS health_alerts (
                search_id TEXT PRIMARY KEY,
                sent_at   TEXT
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


def is_url_known(url: str) -> bool:
    """Return True if this URL already exists in the properties table.

    Used as a fallback dedup check: if alerts_sent is wiped (SQLite restart),
    this prevents re-alerting for properties already stored in the DB.
    """
    with _db() as conn:
        row = _x(conn,
            "SELECT 1 FROM properties WHERE url = ?",
            (url,),
        ).fetchone()
        return row is not None


def get_stored_price(property_id: str) -> Optional[int]:
    """Return the current stored price for a property, or None if not in the DB."""
    with _db() as conn:
        row = _x(conn,
            "SELECT price FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()
        if row is None:
            return None
        return row["price"]


def save_property(prop: Property) -> None:
    """Upsert a property into the database."""
    params = (
        prop.id, prop.source, prop.listing_type, prop.url,
        prop.price, prop.previous_price,
        prop.bedrooms, prop.property_type, prop.address,
        prop.title, prop.postcode, prop.image_url,
        prop.epc_rating, prop.crime_rate, prop.commute_minutes,
        prop.nearest_school, prop.school_rating, prop.agent_name,
        prop.nearest_station, prop.station_distance_miles,
        prop.first_seen.isoformat(),
    )
    with _db() as conn:
        if _USE_PG:
            _x(conn, """
                INSERT INTO properties
                    (id, source, listing_type, url, price, previous_price,
                     bedrooms, property_type, address, title, postcode, image_url,
                     epc_rating, crime_rate, commute_minutes,
                     nearest_school, school_rating, agent_name,
                     nearest_station, station_distance_miles, first_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (id) DO UPDATE SET
                    source                 = EXCLUDED.source,
                    listing_type           = EXCLUDED.listing_type,
                    url                    = EXCLUDED.url,
                    price                  = EXCLUDED.price,
                    previous_price         = EXCLUDED.previous_price,
                    bedrooms               = EXCLUDED.bedrooms,
                    property_type          = EXCLUDED.property_type,
                    address                = EXCLUDED.address,
                    title                  = EXCLUDED.title,
                    postcode               = EXCLUDED.postcode,
                    image_url              = EXCLUDED.image_url,
                    epc_rating             = EXCLUDED.epc_rating,
                    crime_rate             = EXCLUDED.crime_rate,
                    commute_minutes        = EXCLUDED.commute_minutes,
                    nearest_school         = EXCLUDED.nearest_school,
                    school_rating          = EXCLUDED.school_rating,
                    agent_name             = EXCLUDED.agent_name,
                    nearest_station        = EXCLUDED.nearest_station,
                    station_distance_miles = EXCLUDED.station_distance_miles
            """, params)
        else:
            _x(conn, """
                INSERT OR REPLACE INTO properties
                    (id, source, listing_type, url, price, previous_price,
                     bedrooms, property_type, address, title, postcode, image_url,
                     epc_rating, crime_rate, commute_minutes,
                     nearest_school, school_rating, agent_name,
                     nearest_station, station_distance_miles, first_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
    property_type: str = "",
    keyword: str = "",
    favourites_only: bool = False,
    sort: str = "newest",
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
    if property_type:
        conditions.append("LOWER(COALESCE(p.property_type,'')) LIKE ?")
        params.append(f"%{property_type.lower()}%")
    if keyword:
        conditions.append("(p.address LIKE ? OR p.title LIKE ?)")
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")

    where = "WHERE " + " AND ".join(conditions)

    # Nulls/zeros sort to the end for price sorts
    _zero_last = "CASE WHEN p.price IS NULL OR p.price = 0 THEN 1 ELSE 0 END"
    order_by = {
        "price_asc":  f"{_zero_last}, p.price ASC",
        "price_desc": f"{_zero_last}, p.price DESC",
    }.get(sort, "p.first_seen DESC")

    params.append(limit)

    with _db() as conn:
        return _x(conn, f"""
            SELECT p.*,
                   COALESCE(u.favourited, 0) AS favourited,
                   COALESCE(u.hidden, 0)     AS hidden
            FROM properties p
            LEFT JOIN user_marks u ON p.id = u.property_id
            {where}
            ORDER BY {order_by}
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


def get_health_alert_sent(search_id: str) -> Optional[datetime]:
    """Return when the last health alert was sent for this search, or None."""
    with _db() as conn:
        row = _x(conn,
            "SELECT sent_at FROM health_alerts WHERE search_id = ?",
            (search_id,),
        ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(row["sent_at"])
        except Exception:
            return None


def set_health_alert_sent(search_id: str) -> None:
    """Record that a health alert was just sent for this search."""
    now = datetime.now().isoformat()
    with _db() as conn:
        if _USE_PG:
            _x(conn,
                "INSERT INTO health_alerts (search_id, sent_at) VALUES (?, ?)"
                " ON CONFLICT (search_id) DO UPDATE SET sent_at = EXCLUDED.sent_at",
                (search_id, now),
            )
        else:
            _x(conn,
                "INSERT OR REPLACE INTO health_alerts (search_id, sent_at) VALUES (?, ?)",
                (search_id, now),
            )


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
