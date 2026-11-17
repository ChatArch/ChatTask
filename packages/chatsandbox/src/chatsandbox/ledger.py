"""Private durable reservations. Expiry alone never proves a workload is gone."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .auth import AccessDenied


class Ledger:
    def __init__(self, database):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (self.path.parent.is_symlink() or self.path.parent.stat().st_mode & 0o077 or
                self.path.is_symlink() or self.path.exists() and self.path.stat().st_mode & 0o077):
            raise ValueError('Sandbox state requires private non-symlink storage')
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS instances (
                request_id TEXT PRIMARY KEY, caller_id TEXT NOT NULL, key_id TEXT NOT NULL,
                sandbox_id TEXT UNIQUE, state TEXT NOT NULL, image TEXT NOT NULL,
                cpu_millis INTEGER NOT NULL, memory_mib INTEGER NOT NULL,
                created REAL NOT NULL, expires_at REAL NOT NULL, max_deadline REAL NOT NULL,
                metadata TEXT NOT NULL, body_hash TEXT NOT NULL)''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def reserve(self, grant, body, *, image, cpu, memory, ttl, idempotency=None):
        if ttl <= 0 or ttl > grant.max_ttl_seconds: raise AccessDenied(400, 'sandbox_ttl_invalid')
        if image not in grant.images: raise AccessDenied(403, 'sandbox_image_denied')
        if idempotency is not None and (not isinstance(idempotency,str) or not 1 <= len(idempotency) <= 128 or any(ord(c)<33 or ord(c)>126 for c in idempotency)):
            raise AccessDenied(400, 'invalid_idempotency_key')
        request_id = hashlib.sha256((grant.caller_id + '\0' + idempotency).encode()).hexdigest()[:32] if idempotency else uuid.uuid4().hex
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',',':')).encode()).hexdigest()
        now = time.time()
        deadline = min(now + grant.max_ttl_seconds, grant.expires_at or float('inf'))
        if now + ttl > deadline + .01: raise AccessDenied(400, 'sandbox_ttl_invalid')
        with self.transaction() as db:
            old = db.execute('SELECT * FROM instances WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['caller_id'] != grant.caller_id or old['body_hash'] != fingerprint:
                    raise AccessDenied(409, 'idempotency_conflict')
                return dict(old), False
            used = db.execute("SELECT COUNT(*),COALESCE(SUM(cpu_millis),0),COALESCE(SUM(memory_mib),0) FROM instances WHERE caller_id=? AND state!='absent'", (grant.caller_id,)).fetchone()
            if used[0] + 1 > grant.max_sandboxes or used[1] + cpu > grant.max_cpu_millis or used[2] + memory > grant.max_memory_mib:
                raise AccessDenied(429, 'sandbox_budget_exhausted')
            db.execute('INSERT INTO instances VALUES (?,?,?,NULL,?,?,?,?,?,?,?,?,?)',
                       (request_id,grant.caller_id,grant.key_id,'pending',image,cpu,memory,now,now+ttl,deadline,json.dumps(body.get('metadata') or {},sort_keys=True),fingerprint))
            return dict(db.execute('SELECT * FROM instances WHERE request_id=?',(request_id,)).fetchone()), True

    def activate(self, request_id, sandbox_id):
        with self.transaction() as db:
            db.execute("UPDATE instances SET sandbox_id=?,state='active' WHERE request_id=? AND state IN ('pending','uncertain')",(sandbox_id,request_id))

    def state(self, request_id, state):
        if state not in {'pending','uncertain','active','deleting','absent'}: raise ValueError('Invalid state')
        with self.transaction() as db: db.execute('UPDATE instances SET state=? WHERE request_id=?',(state,request_id))

    def owned(self, sandbox_id, caller_id):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM instances WHERE sandbox_id=? AND caller_id=?',(sandbox_id,caller_id)).fetchone()
        if row is None: raise AccessDenied(404, 'sandbox_not_found')
        return dict(row)

    def all(self, caller_id=None):
        with self.transaction() as db:
            rows = db.execute('SELECT * FROM instances' + (' WHERE caller_id=?' if caller_id is not None else ''), (caller_id,) if caller_id is not None else ()).fetchall()
        return [dict(r) for r in rows]

    def operation(self,request_id,caller_id):
        with self.transaction() as db:
            row=db.execute('SELECT request_id,sandbox_id,state FROM instances WHERE request_id=? AND caller_id=?',(request_id,caller_id)).fetchone()
        if row is None: raise AccessDenied(404,'sandbox_request_not_found')
        return dict(row)

    def renewed(self, request_id, expires_at):
        with self.transaction() as db: db.execute('UPDATE instances SET expires_at=? WHERE request_id=?',(expires_at,request_id))
