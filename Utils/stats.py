from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta

SOLD = ("CONFIRMED", "CONFIRMED_AUTOMATICALLY")
WORK = ("PAID", "PENDING", "SENT")
REFUND = ("ROLLED_BACK",)
FIELDS = ("created_at", "completed_at", "status", "has_problem", "item_name", "game", "category", "price", "buyer")
PERIOD_DAYS = {"d": 0, "w": 6, "m": 29}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id TEXT PRIMARY KEY, created_at REAL, completed_at REAL, status TEXT, has_problem INTEGER,
    item_name TEXT, game TEXT, category TEXT, price INTEGER, buyer TEXT, updated_at REAL
);
CREATE INDEX IF NOT EXISTS sales_created ON sales(created_at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

UPSERT = f"""
INSERT INTO sales (id, {", ".join(FIELDS)}, updated_at) VALUES (?, {", ".join("?" for _ in FIELDS)}, ?)
ON CONFLICT(id) DO UPDATE SET
    created_at = COALESCE(sales.created_at, excluded.created_at),
    status = CASE WHEN excluded.status IN ({", ".join(f"'{s}'" for s in WORK)})
                   AND sales.status IN ({", ".join(f"'{s}'" for s in SOLD + REFUND)})
             THEN sales.status ELSE COALESCE(excluded.status, sales.status) END,
    {", ".join(f"{f} = COALESCE(excluded.{f}, sales.{f})" for f in FIELDS if f not in ("created_at", "status"))},
    updated_at = excluded.updated_at
"""


def _in(values: tuple) -> str:
    return ", ".join(f"'{v}'" for v in values)


def to_ts(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _name(obj) -> str | None:
    return getattr(obj, "name", None) if obj is not None else None


def deal_row(deal) -> dict:
    item = getattr(deal, "item", None)
    status = getattr(deal, "status", None)
    return {
        "id": deal.id,
        "created_at": to_ts(getattr(deal, "created_at", None)),
        "completed_at": to_ts(getattr(deal, "completed_at", None)),
        "status": getattr(status, "name", status),
        "has_problem": getattr(deal, "has_problem", None),
        "item_name": getattr(item, "name", None),
        "game": _name(getattr(item, "game", None)),
        "category": _name(getattr(item, "category", None)),
        "price": getattr(item, "price", None),
        "buyer": getattr(getattr(deal, "user", None), "username", None),
    }


def period_start(code: str, now: float) -> float | None:
    if code not in PERIOD_DAYS:
        return None
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - timedelta(days=PERIOD_DAYS[code])).timestamp()


class SalesJournal:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._lock, closing(self._connect()) as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def record(self, row: dict, status: str | None = None, has_problem: bool | None = None) -> None:
        values = {f: row.get(f) for f in FIELDS}
        if status is not None:
            values["status"] = status
        if has_problem is not None:
            values["has_problem"] = has_problem
        if values["has_problem"] is not None:
            values["has_problem"] = int(bool(values["has_problem"]))
        now = time.time()
        if values["created_at"] is None:
            values["created_at"] = now
        params = [row["id"], *(values[f] for f in FIELDS), now]
        with self._lock, closing(self._connect()) as db, db:
            db.execute(UPSERT, params)

    def get(self, deal_id: str) -> dict | None:
        with self._lock, closing(self._connect()) as db:
            found = db.execute("SELECT * FROM sales WHERE id = ?", (deal_id,)).fetchone()
        if found is None:
            return None
        result = dict(found)
        if result["has_problem"] is not None:
            result["has_problem"] = bool(result["has_problem"])
        return result

    def meta(self, key: str) -> str | None:
        with self._lock, closing(self._connect()) as db:
            found = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return found["value"] if found else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (key, value))

    def summary(self, start: float | None) -> dict:
        query = f"""
            SELECT
                SUM(status IN ({_in(SOLD)})), SUM(CASE WHEN status IN ({_in(SOLD)}) THEN COALESCE(price, 0) END),
                SUM(status IN ({_in(WORK)})), SUM(CASE WHEN status IN ({_in(WORK)}) THEN COALESCE(price, 0) END),
                SUM(status IN ({_in(WORK)}) AND has_problem = 1),
                SUM(status IN ({_in(REFUND)})), SUM(CASE WHEN status IN ({_in(REFUND)}) THEN COALESCE(price, 0) END)
            FROM sales WHERE (? IS NULL OR created_at >= ?)
        """
        with self._lock, closing(self._connect()) as db:
            values = [int(v or 0) for v in db.execute(query, (start, start)).fetchone()]
        sold, revenue, work, work_sum, work_problem, refunds, refund_sum = values
        return {"sold": sold, "revenue": revenue, "avg": round(revenue / sold) if sold else 0, "work": work,
                "work_sum": work_sum, "work_problem": work_problem, "refunds": refunds, "refund_sum": refund_sum}

    def top_items(self, start: float | None, limit: int = 5) -> list[tuple[str, int, int]]:
        query = f"""
            SELECT COALESCE(item_name, '?'), COUNT(*), SUM(COALESCE(price, 0)) FROM sales
            WHERE status IN ({_in(SOLD)}) AND (? IS NULL OR created_at >= ?)
            GROUP BY COALESCE(item_name, '?') ORDER BY 3 DESC, 2 DESC LIMIT ?
        """
        with self._lock, closing(self._connect()) as db:
            return [(r[0], int(r[1]), int(r[2] or 0)) for r in db.execute(query, (start, start, limit)).fetchall()]

    def by_day(self, days: int, now: float) -> list[tuple[str, int, int]]:
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        first = midnight - timedelta(days=days - 1)
        buckets = {(first + timedelta(days=i)).strftime("%d.%m"): [0, 0] for i in range(days)}
        query = f"SELECT created_at, COALESCE(price, 0) FROM sales WHERE status IN ({_in(SOLD)}) AND created_at >= ?"
        with self._lock, closing(self._connect()) as db:
            rows = db.execute(query, (first.timestamp(),)).fetchall()
        for created_at, price in rows:
            key = datetime.fromtimestamp(created_at).strftime("%d.%m")
            if key in buckets:
                buckets[key][0] += 1
                buckets[key][1] += int(price)
        return [(day, count, total) for day, (count, total) in buckets.items()]

    def in_work_ids(self, since: float) -> list[str]:
        query = f"SELECT id FROM sales WHERE status IN ({_in(WORK)}) AND created_at >= ? ORDER BY created_at"
        with self._lock, closing(self._connect()) as db:
            return [r[0] for r in db.execute(query, (since,)).fetchall()]
