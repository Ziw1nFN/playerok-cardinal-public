from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
import base64
from contextlib import contextmanager
from pathlib import Path


class ControlStore:
    def __init__(self, path: str):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS installations (
                    id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, created REAL NOT NULL,
                    last_seen REAL NOT NULL, plugins TEXT NOT NULL DEFAULT '[]',
                    blocked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, digest TEXT NOT NULL, content BLOB NOT NULL, created REAL NOT NULL
                );
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(installations)')}
            if 'owners' not in columns:
                db.execute("ALTER TABLE installations ADD COLUMN owners TEXT NOT NULL DEFAULT '[]'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    def register(self) -> tuple[str, str]:
        ident, secret, now = str(uuid.uuid4()), secrets.token_urlsafe(32), time.time()
        with self.connect() as db:
            db.execute('INSERT INTO installations(id,secret_hash,created,last_seen) VALUES(?,?,?,?)',
                       (ident, self.digest(secret), now, now))
        return ident, secret

    def authenticate(self, ident: str, secret: str) -> bool:
        with self.connect() as db:
            row = db.execute('SELECT secret_hash FROM installations WHERE id=?', (ident,)).fetchone()
        return bool(row and hmac.compare_digest(row[0], self.digest(secret)))

    def heartbeat(self, ident: str, secret: str, plugins: list[str], after: int, owners: list[int] | None = None) -> dict:
        if not self.authenticate(ident, secret):
            raise PermissionError('Invalid installation credentials')
        if len(plugins) > 200 or any(not isinstance(p, str) or len(p) > 128 for p in plugins):
            raise ValueError('Invalid plugin list')
        owners = owners or []
        if len(owners) > 20 or any(type(owner) is not int or owner <= 0 for owner in owners):
            raise ValueError('Invalid owners')
        with self.connect() as db:
            db.execute('UPDATE installations SET last_seen=?, plugins=?, owners=? WHERE id=?',
                       (time.time(), json.dumps(plugins, ensure_ascii=False), json.dumps(owners), ident))
            blocked = bool(db.execute('SELECT blocked FROM installations WHERE id=?', (ident,)).fetchone()[0])
            messages = [] if blocked else db.execute(
                'SELECT id,text FROM broadcasts WHERE id>? ORDER BY id LIMIT 50', (after,)).fetchall()
            release = None if blocked else db.execute('SELECT id,digest FROM releases ORDER BY id DESC LIMIT 1').fetchone()
        return {'blocked': blocked, 'messages': [{'id': i, 'text': text} for i, text in messages],
                'release': {'id': release[0], 'digest': release[1]} if release else None}

    def publish_release(self, content: bytes) -> int:
        if not content or len(content) > 15 * 1024 * 1024:
            raise ValueError('Invalid release size')
        digest = hashlib.sha256(content).hexdigest()
        with self.connect() as db:
            return db.execute('INSERT INTO releases(digest,content,created) VALUES(?,?,?)',
                              (digest, content, time.time())).lastrowid

    def download_release(self, ident: str, secret: str, release_id: int) -> dict:
        if not self.authenticate(ident, secret):
            raise PermissionError('Invalid installation credentials')
        with self.connect() as db:
            blocked = db.execute('SELECT blocked FROM installations WHERE id=?', (ident,)).fetchone()[0]
            row = db.execute('SELECT digest,content FROM releases WHERE id=?', (release_id,)).fetchone()
        if blocked or row is None:
            raise PermissionError('Release unavailable')
        return {'digest': row[0], 'content': base64.b64encode(row[1]).decode('ascii')}

    def set_blocked(self, ident: str, blocked: bool) -> bool:
        with self.connect() as db:
            return db.execute('UPDATE installations SET blocked=? WHERE id=?', (int(blocked), ident)).rowcount == 1

    def broadcast(self, text: str) -> int:
        if not text.strip() or len(text) > 3500:
            raise ValueError('Invalid message')
        with self.connect() as db:
            return db.execute('INSERT INTO broadcasts(text,created) VALUES(?,?)', (text, time.time())).lastrowid

    def latest_broadcast(self) -> int:
        with self.connect() as db:
            return db.execute('SELECT COALESCE(MAX(id),0) FROM broadcasts').fetchone()[0]

    def installations(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute('SELECT id,created,last_seen,plugins,blocked,owners FROM installations ORDER BY last_seen DESC').fetchall()
        return [{'id': i, 'created': created, 'last_seen': seen, 'plugins': json.loads(plugins),
                 'blocked': bool(blocked), 'owners': json.loads(owners)} for i, created, seen, plugins, blocked, owners in rows]
