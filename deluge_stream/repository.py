"""Product policy storage. Synchronous I/O: call only from the DB worker.

This is not the phase-0 QoS ledger. The lifecycle service supplies live-instance
keys and resets all bindings on explicit plugin disable. Native daemon restore
can retain records; fresh live adds never inherit them. No payload deletion.
"""

import json
import math
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from .config import DEFAULTS, validate

MAX_INTEGER = 2**63 - 1
MAX_TEXT = 65536
METADATA_FIELDS = frozenset({"title", "poster", "category", "data"})
MIGRATIONS = (
    (
        "CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL)",
        "INSERT INTO counters VALUES ('fifo', 0)",
        "CREATE TABLE torrent_policy (record_id TEXT PRIMARY KEY, "
        "torrent_id TEXT NOT NULL UNIQUE, instance_key TEXT NOT NULL UNIQUE, "
        "owner TEXT NOT NULL, is_temporary INTEGER NOT NULL DEFAULT 0 "
        "CHECK(is_temporary IN (0,1)), fifo_sequence INTEGER UNIQUE, "
        "published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0,1)), "
        "revision INTEGER NOT NULL DEFAULT 0, "
        "created_at INTEGER NOT NULL DEFAULT (unixepoch()), "
        "updated_at INTEGER NOT NULL DEFAULT (unixepoch()), "
        "CHECK((is_temporary=0 AND fifo_sequence IS NULL) OR "
        "(is_temporary=1 AND fifo_sequence > 0)))",
        "CREATE TABLE aliases (alias TEXT PRIMARY KEY, record_id TEXT NOT NULL "
        "REFERENCES torrent_policy(record_id) ON DELETE CASCADE)",
        "CREATE TABLE torrent_metadata (record_id TEXT PRIMARY KEY REFERENCES "
        "torrent_policy(record_id) ON DELETE CASCADE, "
        "body TEXT NOT NULL DEFAULT '{}')",
        "CREATE TABLE settings (name TEXT PRIMARY KEY, body TEXT NOT NULL)",
        "CREATE TABLE operation_journal (operation_id TEXT PRIMARY KEY, "
        "record_id TEXT NOT NULL, kind TEXT NOT NULL, stage TEXT NOT NULL, "
        "body TEXT NOT NULL)",
    ),
    (
        "CREATE TABLE viewed (record_id TEXT NOT NULL REFERENCES "
        "torrent_policy(record_id) ON DELETE CASCADE, file_id INTEGER NOT NULL "
        "CHECK(file_id > 0), timecode REAL NOT NULL CHECK(timecode >= 0), "
        "PRIMARY KEY(record_id, file_id))",
    ),
    (
        "CREATE TABLE lifecycle (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
        "reset_count INTEGER NOT NULL DEFAULT 0)",
        "INSERT INTO lifecycle(singleton) VALUES (1)",
    ),
    (
        "ALTER TABLE lifecycle ADD COLUMN dirty INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE lifecycle ADD COLUMN recovery_required INTEGER NOT NULL DEFAULT 0",
        "UPDATE lifecycle SET recovery_required=CASE WHEN EXISTS "
        "(SELECT 1 FROM torrent_policy) THEN 1 ELSE 0 END",
    ),
)


class RepositoryError(RuntimeError):
    pass


class SchemaError(RepositoryError):
    pass


class Conflict(RepositoryError):
    """Stale record/revision or an alias belonging to another instance."""


def text(value, name, *, limit=MAX_TEXT):
    if not isinstance(value, str) or not value or len(value.encode()) > limit:
        raise ValueError(f"invalid {name}")
    return value


def boolean(value):
    if type(value) is not bool:
        raise ValueError("expected boolean")
    return value


def positive_integer(value):
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:
        raise ValueError("expected positive integer")
    return value


def valid_alias(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value
    ):
        raise ValueError("invalid hash alias")
    return value


def encode(value):
    result = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(result.encode()) > MAX_TEXT:
        raise ValueError("JSON exceeds storage limit")
    return result


class Repository:
    SCHEMA = len(MIGRATIONS)
    PAGE_LIMIT = 128

    def __init__(self, path, *, busy_timeout_ms=5000):
        self.path = Path(path)
        self.busy_timeout_ms = positive_integer(busy_timeout_ms)
        if self.busy_timeout_ms > 30000:
            raise ValueError("busy timeout exceeds worker limit")

    def _connect(self):
        db = sqlite3.connect(
            self.path, isolation_level=None, timeout=self.busy_timeout_ms / 1000
        )
        try:
            db.row_factory = sqlite3.Row
            db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            db.close()
            raise
        return db

    def initialize(self):
        """Sequential atomic migrations; unknown schemas are never rewritten."""
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            self._check_version(version)
            if (
                version == 0
                and db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise SchemaError("unknown unversioned database")
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise RepositoryError("SQLite WAL is required")
            db.execute("BEGIN IMMEDIATE")
            try:
                # Recheck after the writer lock, including concurrent initialization.
                version = db.execute("PRAGMA user_version").fetchone()[0]
                self._check_version(version)
                if (
                    version == 0
                    and db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                    ).fetchone()
                ):
                    raise SchemaError("unknown unversioned database")
                for index in range(version, self.SCHEMA):
                    for statement in MIGRATIONS[index]:
                        db.execute(statement)
                    db.execute(f"PRAGMA user_version={index + 1}")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _check_version(self, version):
        if not 0 <= version <= self.SCHEMA:
            raise SchemaError("unsupported policy database schema")

    @contextmanager
    def _transaction(self, *, write=False):
        with closing(self._connect()) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != self.SCHEMA:
                raise SchemaError("policy database needs initialization")
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise RepositoryError("SQLite WAL is required")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _record(db, record_id, revision=None):
        row = db.execute(
            "SELECT * FROM torrent_policy WHERE record_id=?", (record_id,)
        ).fetchone()
        if row is None or (revision is not None and row["revision"] != revision):
            raise Conflict("torrent instance or revision changed")
        return dict(row)

    @staticmethod
    def _aliases(db, record_id, aliases):
        for alias in aliases:
            existing = db.execute(
                "SELECT record_id FROM aliases WHERE alias=?", (alias,)
            ).fetchone()
            if existing is not None and existing[0] != record_id:
                raise Conflict("alias belongs to another instance")
            db.execute(
                "INSERT OR IGNORE INTO aliases VALUES (?, ?)", (alias, record_id)
            )

    def register(self, torrent_id, instance_key, owner, *, aliases=()):
        """Register an ordinary Deluge torrent as permanent and unpublished.

        Repeated observations preserve policy. An explicit identity mismatch
        must be reconciled first; it cannot silently inherit another policy.
        """
        valid_alias(torrent_id)
        if len(torrent_id) != 40:
            raise ValueError("invalid Deluge torrent id")
        text(instance_key, "instance key", limit=512)
        text(owner, "owner", limit=256)
        aliases = tuple(aliases)
        if len(aliases) > 3:
            raise ValueError("too many aliases")
        aliases = tuple(valid_alias(alias) for alias in aliases)
        with self._transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM torrent_policy WHERE torrent_id=?", (torrent_id,)
            ).fetchone()
            if row is not None:
                if row["instance_key"] != instance_key or row["owner"] != owner:
                    raise Conflict("torrent identity or owner changed")
                record_id = row["record_id"]
            else:
                record_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO torrent_policy "
                    "(record_id,torrent_id,instance_key,owner) VALUES (?,?,?,?)",
                    (record_id, torrent_id, instance_key, owner),
                )
                db.execute(
                    "INSERT INTO torrent_metadata(record_id) VALUES (?)", (record_id,)
                )
            self._aliases(db, record_id, (torrent_id, *aliases))
            return self._record(db, record_id)

    def get(self, record_id):
        with self._transaction() as db:
            return self._record(db, record_id)

    def resolve(self, alias):
        valid_alias(alias)
        with self._transaction() as db:
            row = db.execute(
                "SELECT record_id FROM aliases WHERE alias=?", (alias,)
            ).fetchone()
            return None if row is None else self._record(db, row[0])

    def scan(self, *, after="", limit=128):
        if type(limit) is not int or not 1 <= limit <= self.PAGE_LIMIT:
            raise ValueError("invalid page limit")
        with self._transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM torrent_policy WHERE record_id>? "
                    "ORDER BY record_id LIMIT ?",
                    (after, limit),
                )
            ]

    def _change(self, db, record_id, *, temporary=None, published=None, revision=None):
        row = self._record(db, record_id, revision)
        target = row["is_temporary"] if temporary is None else int(temporary)
        public = row["published"] if published is None else int(published)
        if (target, public) == (row["is_temporary"], row["published"]):
            return row
        fifo = row["fifo_sequence"]
        if not target:
            fifo = None
        elif not row["is_temporary"]:
            counter = db.execute(
                "SELECT value FROM counters WHERE name='fifo'"
            ).fetchone()[0]
            if counter >= MAX_INTEGER:
                raise RepositoryError("FIFO sequence exhausted")
            fifo = counter + 1
            db.execute("UPDATE counters SET value=? WHERE name='fifo'", (fifo,))
        db.execute(
            "UPDATE torrent_policy SET is_temporary=?,fifo_sequence=?,"
            "published=?,revision=revision+1,updated_at=unixepoch() WHERE record_id=?",
            (target, fifo, public, record_id),
        )
        # A DB-only change is one transaction, not a fake Deluge/SQLite transaction.
        # Completed transitions are not an unbounded audit log.
        db.execute(
            "DELETE FROM operation_journal WHERE record_id=? AND "
            "kind='policy' AND stage='committed'",
            (record_id,),
        )
        db.execute(
            "INSERT INTO operation_journal VALUES (?,?,'policy','committed',?)",
            (
                str(uuid.uuid4()),
                record_id,
                encode(
                    {
                        "temporary": bool(target),
                        "published": bool(public),
                        "revision": row["revision"] + 1,
                    }
                ),
            ),
        )
        return self._record(db, record_id)

    def set_policy(self, record_id, *, temporary=None, published=None, revision=None):
        """Storage primitive, not an authorization/path-ownership admission check."""
        if temporary is not None:
            boolean(temporary)
        if published is not None:
            boolean(published)
        if revision is not None and (type(revision) is not int or revision < 0):
            raise ValueError("invalid revision")
        with self._transaction(write=True) as db:
            return self._change(
                db,
                record_id,
                temporary=temporary,
                published=published,
                revision=revision,
            )

    def repeat_add(self, record_id, *, save_to_db, revision=None):
        """Existing add publishes; it can promote but never demote permanence."""
        boolean(save_to_db)
        if revision is not None and (type(revision) is not int or revision < 0):
            raise ValueError("invalid revision")
        with self._transaction(write=True) as db:
            return self._change(
                db,
                record_id,
                temporary=False if save_to_db else None,
                published=True,
                revision=revision,
            )

    def forget(self, record_id):
        """Only after lifecycle proves absence/replacement; never deletes files."""
        with self._transaction(write=True) as db:
            db.execute("DELETE FROM operation_journal WHERE record_id=?", (record_id,))
            return (
                db.execute(
                    "DELETE FROM torrent_policy WHERE record_id=?", (record_id,)
                ).rowcount
                == 1
            )

    def set_metadata(self, record_id, changes):
        if not isinstance(changes, dict) or changes.keys() - METADATA_FIELDS:
            raise ValueError("invalid metadata fields")
        if any(not isinstance(value, str) for value in changes.values()):
            raise ValueError("metadata fields must be strings")
        with self._transaction(write=True) as db:
            self._record(db, record_id)
            current = json.loads(
                db.execute(
                    "SELECT body FROM torrent_metadata WHERE record_id=?", (record_id,)
                ).fetchone()[0]
            )
            current.update(changes)
            db.execute(
                "UPDATE torrent_metadata SET body=? WHERE record_id=?",
                (encode(current), record_id),
            )
            return current

    def get_metadata(self, record_id):
        with self._transaction() as db:
            self._record(db, record_id)
            return json.loads(
                db.execute(
                    "SELECT body FROM torrent_metadata WHERE record_id=?", (record_id,)
                ).fetchone()[0]
            )

    def set_viewed(self, record_id, file_id, timecode):
        positive_integer(file_id)
        if (
            type(timecode) not in (float, int)
            or not 0 <= timecode <= MAX_INTEGER
            or not math.isfinite(timecode)
        ):
            raise ValueError("invalid timecode")
        with self._transaction(write=True) as db:
            self._record(db, record_id)
            db.execute(
                "INSERT INTO viewed VALUES (?,?,?) ON CONFLICT(record_id,file_id) "
                "DO UPDATE SET timecode=excluded.timecode",
                (record_id, file_id, timecode),
            )

    def list_viewed(self, record_id, *, after=0, limit=128):
        if type(after) is not int or after < 0:
            raise ValueError("invalid viewed cursor")
        if type(limit) is not int or not 1 <= limit <= self.PAGE_LIMIT:
            raise ValueError("invalid page limit")
        with self._transaction() as db:
            self._record(db, record_id)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT file_id,timecode FROM viewed "
                    "WHERE record_id=? AND file_id>? "
                    "ORDER BY file_id LIMIT ?",
                    (record_id, after, limit),
                )
            ]

    def remove_viewed(self, record_id, file_id):
        positive_integer(file_id)
        with self._transaction(write=True) as db:
            self._record(db, record_id)
            db.execute(
                "DELETE FROM viewed WHERE record_id=? AND file_id=?",
                (record_id, file_id),
            )

    def set_settings(self, changes):
        validate(changes)
        with self._transaction(write=True) as db:
            for name, value in changes.items():
                db.execute(
                    "INSERT INTO settings VALUES (?,?) ON CONFLICT(name) "
                    "DO UPDATE SET body=excluded.body",
                    (name, encode(value)),
                )

    def get_settings(self):
        with self._transaction() as db:
            result = dict(DEFAULTS)
            result.update(
                (row[0], json.loads(row[1]))
                for row in db.execute("SELECT name,body FROM settings")
            )
            return validate({}, result)

    def reset_bindings(self):
        """Explicit disable: revoke all old instance bindings, not daemon stop."""
        with self._transaction(write=True) as db:
            db.execute("DELETE FROM operation_journal")
            db.execute("DELETE FROM torrent_policy")
            db.execute(
                "UPDATE lifecycle SET reset_count=reset_count+1 WHERE singleton=1"
            )
            db.execute(
                "UPDATE lifecycle SET dirty=0,recovery_required=0 WHERE singleton=1"
            )

    def begin_run(self):
        with self._transaction(write=True) as db:
            db.execute(
                "UPDATE lifecycle SET recovery_required=MAX(recovery_required,dirty),"
                "dirty=1 WHERE singleton=1"
            )
            return bool(
                db.execute("SELECT recovery_required FROM lifecycle").fetchone()[0]
            )

    def finish_run(self):
        with self._transaction(write=True) as db:
            db.execute("UPDATE lifecycle SET dirty=0 WHERE singleton=1")

    def confirm_recovery(self):
        with self._transaction(write=True) as db:
            db.execute("UPDATE lifecycle SET recovery_required=0 WHERE singleton=1")

    def require_recovery(self):
        with self._transaction(write=True) as db:
            db.execute("UPDATE lifecycle SET recovery_required=1 WHERE singleton=1")

    def reconcile(self, entries):
        """Bounded authoritative catalogue. Call only after native session load.

        Each entry is (tid, live_key, owner, aliases, fresh_add). A fresh add or
        owner change cannot inherit a prior record. A daemon reload can rebind
        a saved record to a new in-memory key under the agreed disable contract.
        """
        if len(entries) > 4096 or len({entry[0] for entry in entries}) != len(entries):
            raise RepositoryError("catalogue_limit")
        for tid, key, owner, aliases, fresh in entries:
            if len(valid_alias(tid)) != 40:
                raise ValueError("invalid torrent id")
            text(key, "live key", limit=512)
            text(owner, "owner", limit=256)
            boolean(fresh)
            if len(aliases) > 3:
                raise ValueError("too many aliases")
            for alias in aliases:
                valid_alias(alias)
        with self._transaction(write=True) as db:
            old = {
                row["torrent_id"]: dict(row)
                for row in db.execute("SELECT * FROM torrent_policy LIMIT 4097")
            }
            if len(old) > 4096:
                raise RepositoryError("catalogue_limit")
            live = {entry[0] for entry in entries}
            for tid, row in old.items():
                if tid not in live:
                    db.execute(
                        "DELETE FROM operation_journal WHERE record_id=?",
                        (row["record_id"],),
                    )
                    db.execute(
                        "DELETE FROM torrent_policy WHERE record_id=?",
                        (row["record_id"],),
                    )
            result = {}
            for tid, key, owner, aliases, fresh in entries:
                row = old.get(tid)
                if row is not None and (fresh or row["owner"] != owner):
                    db.execute(
                        "DELETE FROM operation_journal WHERE record_id=?",
                        (row["record_id"],),
                    )
                    db.execute(
                        "DELETE FROM torrent_policy WHERE record_id=?",
                        (row["record_id"],),
                    )
                    row = None
                if row is None:
                    rid = str(uuid.uuid4())
                    db.execute(
                        "INSERT INTO torrent_policy "
                        "(record_id,torrent_id,instance_key,owner) VALUES (?,?,?,?)",
                        (rid, tid, key, owner),
                    )
                    db.execute(
                        "INSERT INTO torrent_metadata(record_id) VALUES (?)", (rid,)
                    )
                else:
                    rid = row["record_id"]
                    db.execute(
                        "UPDATE torrent_policy SET instance_key=? WHERE record_id=?",
                        (key, rid),
                    )
                self._aliases(db, rid, (tid, *aliases))
                result[tid] = self._record(db, rid)
            return result
