"""Phase-0 SQLite candidate: retain recovery coverage after acknowledgement.

Not wired into Core. All methods perform I/O and MUST run in a bounded worker
queue. One connection per call, no Deluge/native objects stored in this class.
Ownership is retired only by a future proven instance-reconciliation protocol;
acknowledgement deliberately never deletes recovery coverage.
"""

import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path

from .witness import MetadataWitness

MAX_EPOCH = 2**63 - 1


class LedgerError(RuntimeError):
    pass


class LedgerSchemaError(LedgerError):
    pass


class LedgerLimit(LedgerError):
    pass


@dataclass(frozen=True)
class PieceOwnership:
    record_id: str
    torrent_id: str
    witness: MetadataWitness
    piece_count: int
    piece: int
    epoch: int
    pending: bool


def positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def valid_identity(torrent_id, witness):
    if not re.fullmatch(r"[0-9a-f]{40}", torrent_id):
        raise ValueError("invalid Deluge torrent id")
    if str(uuid.UUID(witness.token)) != witness.token:
        raise ValueError("invalid witness record id")


class RecoveryLedger:
    SCHEMA = 1
    PAGE_LIMIT = 128
    ARM_LIMIT = 4096

    def __init__(self, path, *, max_instances=128, max_owned=1_000_000):
        self.path = Path(path)
        self.max_instances = positive(max_instances, "max_instances")
        self.max_owned = positive(max_owned, "max_owned")

    @contextmanager
    def _connection(self, *, write=False):
        with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as db:
            db.execute("PRAGMA busy_timeout=5000")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, self.SCHEMA):
                raise LedgerSchemaError("unsupported recovery-ledger schema")
            if (
                version == 0
                and db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
            ):
                raise LedgerSchemaError("refusing an unknown unversioned database")
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise LedgerError("recovery ledger requires WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            if version == 0:
                db.execute("BEGIN IMMEDIATE")
                try:
                    current = db.execute("PRAGMA user_version").fetchone()[0]
                    if current == 0:
                        db.execute(
                            "CREATE TABLE generations (record_id TEXT PRIMARY KEY, "
                            "tid TEXT NOT NULL, witness TEXT NOT NULL, "
                            "piece_count INTEGER NOT NULL CHECK(piece_count > 0))"
                        )
                        db.execute(
                            "CREATE TABLE owned (record_id TEXT NOT NULL REFERENCES "
                            "generations(record_id), "
                            "piece INTEGER NOT NULL CHECK(piece >= 0), "
                            "epoch INTEGER NOT NULL CHECK(epoch > 0), "
                            "pending INTEGER NOT NULL CHECK(pending IN (0,1)), "
                            "PRIMARY KEY(record_id, piece))"
                        )
                        db.execute("PRAGMA user_version=1")
                    elif current != self.SCHEMA:
                        raise LedgerSchemaError("schema changed during initialization")
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            if write:
                db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                if write:
                    db.commit()
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise

    def register(self, torrent_id, witness, piece_count):
        valid_identity(torrent_id, witness)
        positive(piece_count, "piece_count")
        if piece_count > self.max_owned:
            raise LedgerLimit("metadata exceeds research ledger piece budget")
        encoded = json.dumps(asdict(witness), sort_keys=True)
        with self._connection(write=True) as db:
            old = db.execute(
                "SELECT tid, witness, piece_count FROM generations WHERE record_id=?",
                (witness.token,),
            ).fetchone()
            if old is not None:
                if old != (torrent_id, encoded, piece_count):
                    raise LedgerError("record id already belongs to another instance")
                return witness.token
            if (
                db.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
                >= self.max_instances
            ):
                raise LedgerLimit("research ledger instance budget exhausted")
            db.execute(
                "INSERT INTO generations VALUES (?,?,?,?)",
                (witness.token, torrent_id, encoded, piece_count),
            )
        return witness.token

    def arm(self, record_id, pieces):
        """Durably cover pieces BEFORE deadlines, advancing each admission epoch."""
        pieces = tuple(islice(pieces, self.ARM_LIMIT + 1))
        if (
            not pieces
            or len(pieces) > self.ARM_LIMIT
            or any(type(piece) is not int or piece < 0 for piece in pieces)
        ):
            raise ValueError("invalid or empty piece set")
        pieces = tuple(sorted(set(pieces)))
        with self._connection(write=True) as db:
            generation = db.execute(
                "SELECT piece_count FROM generations WHERE record_id=?", (record_id,)
            ).fetchone()
            if generation is None:
                raise LedgerError("unknown instance")
            if pieces[-1] >= generation[0]:
                raise ValueError("piece outside metadata")
            existing = {}
            for piece in pieces:
                row = db.execute(
                    "SELECT epoch FROM owned WHERE record_id=? AND piece=?",
                    (record_id, piece),
                ).fetchone()
                if row is not None:
                    existing[piece] = row[0]
            added = sum(piece not in existing for piece in pieces)
            if (
                db.execute("SELECT COUNT(*) FROM owned").fetchone()[0] + added
                > self.max_owned
            ):
                raise LedgerLimit("research ledger ownership budget exhausted")
            if any(existing.get(piece, 0) == MAX_EPOCH for piece in pieces):
                raise LedgerError("piece admission epoch exhausted")
            epochs = {}
            for piece in pieces:
                epoch = existing.get(piece, 0) + 1
                db.execute(
                    "INSERT INTO owned VALUES (?,?,?,1) ON CONFLICT(record_id,piece) "
                    "DO UPDATE SET epoch=excluded.epoch, pending=1",
                    (record_id, piece, epoch),
                )
                epochs[piece] = epoch
        return epochs

    def acknowledge(self, record_id, epochs, *, before_commit=None, after_commit=None):
        """CAS completion, not deletion. Hooks are worker-only fault barriers."""
        epochs = dict(epochs)
        if not epochs or any(
            type(p) is not int or p < 0 or type(e) is not int or not 1 <= e <= MAX_EPOCH
            for p, e in epochs.items()
        ):
            raise ValueError("invalid acknowledgement epochs")
        accepted = []
        with self._connection(write=True) as db:
            for piece, epoch in sorted(epochs.items()):
                changed = db.execute(
                    "UPDATE owned SET pending=0 WHERE record_id=? AND piece=? "
                    "AND epoch=? AND pending=1",
                    (record_id, piece, epoch),
                ).rowcount
                if changed:
                    accepted.append(piece)
            if before_commit is not None:
                before_commit()
        if after_commit is not None:
            after_commit()
        return tuple(accepted)

    def scan(self, *, after=None, limit=PAGE_LIMIT):
        """Startup reads ALL ownership, including acknowledged rows, in pages."""
        if type(limit) is not int or not 1 <= limit <= self.PAGE_LIMIT:
            raise ValueError("invalid recovery page size")
        key = after if after is not None else ("", -1)
        with self._connection() as db:
            rows = db.execute(
                "SELECT g.record_id, g.tid, g.witness, g.piece_count, "
                "o.piece, o.epoch, o.pending FROM owned o JOIN generations g "
                "ON g.record_id=o.record_id WHERE (o.record_id,o.piece) > (?,?) "
                "ORDER BY o.record_id,o.piece LIMIT ?",
                (*key, limit),
            ).fetchall()
        result = []
        for rid, tid, raw, count, piece, epoch, pending in rows:
            try:
                witness = MetadataWitness(**json.loads(raw))
                valid_identity(tid, witness)
                if (
                    rid != witness.token
                    or type(count) is not int
                    or count < 1
                    or type(piece) is not int
                    or not 0 <= piece < count
                    or type(epoch) is not int
                    or not 1 <= epoch <= MAX_EPOCH
                    or pending not in (0, 1)
                ):
                    raise ValueError("invalid ownership row")
            except (ValueError, TypeError, AttributeError) as error:
                raise LedgerError("corrupt recovery ownership") from error
            result.append(
                PieceOwnership(rid, tid, witness, count, piece, epoch, bool(pending))
            )
        return result
