"""CycleBites: a small campus surplus food exchange."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(__file__).with_name("cyclebites.db")
POUNDS_PER_PORTION = 0.75  # A transparent planning estimate, not a measured weight.
KG_CO2E_PER_POUND = 0.25  # EPA WARM v16: 0.50 metric tons / short ton landfilled.


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                food_name TEXT NOT NULL,
                portions INTEGER NOT NULL CHECK (portions > 0),
                pickup_location TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'available'
                    CHECK (status IN ('available', 'claimed')),
                created_at TEXT NOT NULL,
                claimed_at TEXT
            )
            """
        )


def add_listing(
    food_name: str,
    portions: int,
    pickup_location: str,
    expires_at: datetime,
    notes: str,
) -> int:
    if not food_name.strip() or not pickup_location.strip():
        raise ValueError("Food name and pickup location are required.")
    if portions < 1:
        raise ValueError("Portions must be greater than zero.")
    if expires_at.tzinfo is None or expires_at.astimezone(timezone.utc) <= utc_now():
        raise ValueError("Pickup deadline must be in the future.")

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO listings
                (food_name, portions, pickup_location, expires_at, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                food_name.strip(),
                portions,
                pickup_location.strip(),
                expires_at.astimezone(timezone.utc).isoformat(),
                notes.strip(),
                utc_now().isoformat(),
            ),
        )
        return int(cursor.lastrowid)


def active_listings() -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute(
            """
            SELECT * FROM listings
            WHERE status = 'available' AND expires_at > ?
            ORDER BY expires_at ASC, id DESC
            """,
            (utc_now().isoformat(),),
        ).fetchall()


def claim_listing(listing_id: int) -> bool:
    """Claim once, atomically, and never claim an expired listing."""
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE listings SET status = 'claimed', claimed_at = ?
            WHERE id = ? AND status = 'available' AND expires_at > ?
            """,
            (utc_now().isoformat(), listing_id, utc_now().isoformat()),
        )
        return cursor.rowcount == 1


def impact_totals() -> sqlite3.Row:
    with connect() as connection:
        return connection.execute(
            """
            SELECT COUNT(*) AS claimed_listings,
                   COALESCE(SUM(portions), 0) AS meals_shared
            FROM listings WHERE status = 'claimed'
            """
        ).fetchone()
