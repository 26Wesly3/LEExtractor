"""SQLite-based disk cache for API responses with TTL."""

import json
import sqlite3
import time
from pathlib import Path


class Cache:
    """Persistent key-value cache with TTL (default 24 hours)."""

    def __init__(self, db_path: str = "", ttl_seconds: int = 86400):
        if not db_path:
            db_path = str(Path(__file__).parent.parent / "litsearch_cache.db")
        self._db_path = db_path
        self._ttl = ttl_seconds
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()

    def _make_key(self, *parts: str) -> str:
        return "|".join(parts)

    def get(self, *key_parts: str):
        key = self._make_key(*key_parts)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT value, created_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        value_str, created_at = row
        if time.time() - created_at > self._ttl:
            self.delete(*key_parts)
            return None
        return json.loads(value_str)

    def set(self, value, *key_parts: str):
        key = self._make_key(*key_parts)
        value_str = json.dumps(value)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
                (key, value_str, time.time()),
            )
            conn.commit()

    def delete(self, *key_parts: str):
        key = self._make_key(*key_parts)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()

    def clear(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()

    def _cleanup_expired(self):
        cutoff = time.time() - self._ttl
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
            conn.commit()
