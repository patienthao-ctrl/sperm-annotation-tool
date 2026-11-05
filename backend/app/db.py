from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any

from .config import DATA_DIR, DB_FILE

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
_DB_LOCK = Lock()

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS annotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  batch_id TEXT,
  object_id TEXT,
  media_id TEXT NOT NULL,
  media_name TEXT,
  media_type TEXT,
  media_width INTEGER,
  media_height INTEGER,
  frame_index INTEGER NOT NULL DEFAULT 0,
  timestamp_ms INTEGER NOT NULL DEFAULT 0,
  object_name TEXT,
  source TEXT,
  confidence REAL,
  shape_type TEXT,
  pct_x REAL, pct_y REAL, pct_w REAL, pct_h REAL,
  px_x1 REAL, px_y1 REAL, px_x2 REAL, px_y2 REAL,
  px_point_x REAL, px_point_y REAL,
  annotation_version TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS media_folder (
  media_id TEXT PRIMARY KEY,
  path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ann_user ON annotations(user_id);
CREATE INDEX IF NOT EXISTS idx_ann_media ON annotations(media_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _DB_LOCK, connect() as conn:
        conn.executescript(CREATE_SQL)
        # 兼容旧数据库
        cols = {row[1] for row in conn.execute("PRAGMA table_info(annotations)")}
        if "batch_id" not in cols:
            conn.execute("ALTER TABLE annotations ADD COLUMN batch_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_batch ON annotations(batch_id)")
        conn.commit()


def get_user(username: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def create_user(username: str, password_hash: str) -> int:
    with _DB_LOCK, connect() as conn:
        cur = conn.execute("INSERT INTO users(username,password_hash) VALUES (?,?)", (username, password_hash))
        conn.commit()
        return int(cur.lastrowid)


def insert_annotations(rows: list[dict[str, Any]]) -> int:
    sql = """
    INSERT INTO annotations (
      user_id,batch_id,object_id,media_id,media_name,media_type,media_width,media_height,
      frame_index,timestamp_ms,object_name,source,confidence,shape_type,
      pct_x,pct_y,pct_w,pct_h,px_x1,px_y1,px_x2,px_y2,px_point_x,px_point_y,
      annotation_version,raw_json
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    with _DB_LOCK, connect() as conn:
        for r in rows:
            # A save is the newest snapshot of one logical object.  Replace an
            # earlier row instead of growing duplicate database records every
            # time the user resumes AI Tracking from the same frame.
            conn.execute(
                """
                DELETE FROM annotations
                WHERE user_id=? AND media_id=? AND frame_index=?
                  AND COALESCE(object_id, '')=COALESCE(?, '')
                  AND COALESCE(source, '')=COALESCE(?, '')
                """,
                (
                    r["user_id"], r["media_id"], r["frame_index"],
                    r["object_id"], r["source"],
                ),
            )
            conn.execute(sql, (
                r["user_id"],r["batch_id"],r["object_id"],r["media_id"],r["media_name"],r["media_type"],
                r["media_width"],r["media_height"],r["frame_index"],r["timestamp_ms"],r["object_name"],
                r["source"],r["confidence"],r["shape_type"],r["pct_x"],r["pct_y"],r["pct_w"],r["pct_h"],
                r["px_x1"],r["px_y1"],r["px_x2"],r["px_y2"],r["px_point_x"],r["px_point_y"],
                r["annotation_version"],r["raw_json"],
            ))
        conn.commit()
    return len(rows)



def list_annotations(user_id: int | None = None, media_id: str | None = None, source: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT a.*, u.username FROM annotations a LEFT JOIN users u ON u.id=a.user_id WHERE 1=1"
    params: list[Any] = []
    if user_id is not None:
        sql += " AND a.user_id=?"
        params.append(user_id)
    if media_id:
        sql += " AND a.media_id=?"
        params.append(media_id)
    if source is not None:
        sql += " AND a.source=?"
        params.append(source)
    sql += " ORDER BY a.id DESC"

    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]

    # Historical versions inserted another row every time AI Tracking was
    # clicked.  Results represent the current logical annotation, not that
    # append-only history: keep only the newest row for the same file/frame/
    # object/user/source. Same filenames are not evidence of the same media.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        media_identity = str(row.get("media_id") or "")
        key = (
            int(row.get("user_id") or 0),
            media_identity,
            str(row.get("media_type") or ""),
            int(row.get("frame_index") or 0),
            str(row.get("object_id") or ""),
            str(row.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def delete_annotation(annotation_id: int, user_id: int) -> bool:
    with _DB_LOCK, connect() as conn:
        cur = conn.execute("DELETE FROM annotations WHERE id=? AND user_id=?", (annotation_id, user_id))
        conn.commit()
        return cur.rowcount > 0
