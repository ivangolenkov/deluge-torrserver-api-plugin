import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Event

import pytest

from deluge_stream.recovery_ledger import (
    MAX_EPOCH,
    LedgerError,
    LedgerLimit,
    LedgerSchemaError,
    RecoveryLedger,
)
from deluge_stream.witness import MetadataWitness

TID = "a" * 40


def witness(number=1):
    return MetadataWitness(str(uuid.UUID(int=number)), 1, number, 16, 123, 456)


def ledger(tmp_path, **kwargs):
    result = RecoveryLedger(tmp_path / "ledger.sqlite", **kwargs)
    rid = result.register(TID, witness(), 8)
    return result, rid


def test_acknowledgement_retains_startup_coverage(tmp_path):
    db, rid = ledger(tmp_path)
    epochs = db.arm(rid, [0, 2])
    assert epochs == {0: 1, 2: 1}
    assert db.acknowledge(rid, epochs) == (0, 2)
    reopened = RecoveryLedger(db.path)
    rows = reopened.scan()
    assert [row.piece for row in rows] == [0, 2]
    assert not any(row.pending for row in rows)
    assert reopened.acknowledge(rid, epochs) == ()


def test_old_completion_cannot_acknowledge_new_deadline_epoch(tmp_path):
    db, rid = ledger(tmp_path)
    old = db.arm(rid, [2])
    new = db.arm(rid, [2])
    assert new == {2: 2}
    assert db.acknowledge(rid, old) == ()
    assert db.scan()[0].pending
    assert db.acknowledge(rid, new) == (2,)


def test_same_hash_generations_are_independent(tmp_path):
    db, old = ledger(tmp_path)
    new = db.register(TID, witness(2), 8)
    old_epoch = db.arm(old, [2])
    new_epoch = db.arm(new, [2])
    db.acknowledge(old, old_epoch)
    rows = db.scan()
    assert [(r.record_id, r.pending) for r in rows] == [(old, False), (new, True)]
    assert db.acknowledge(new, new_epoch) == (2,)


def test_registration_is_idempotent_but_cannot_rebind_record(tmp_path):
    db, rid = ledger(tmp_path)
    assert db.register(TID, witness(), 8) == rid
    with pytest.raises(LedgerError):
        db.register("b" * 40, witness(), 8)
    with pytest.raises(LedgerError):
        db.register(TID, replace(witness(), inode=999), 8)


def test_bad_batch_does_not_partially_arm(tmp_path):
    db, rid = ledger(tmp_path)
    with pytest.raises(ValueError):
        db.arm(rid, [0, 8])
    assert db.scan() == []
    assert db.arm(rid, [0]) == {0: 1}


def test_budget_failure_precedes_deadline_admission(tmp_path):
    db = RecoveryLedger(tmp_path / "ledger.sqlite", max_instances=1, max_owned=2)
    rid = db.register(TID, witness(), 2)
    with pytest.raises(LedgerLimit):
        db.register(TID, witness(2), 2)
    # A second instance cannot bypass a global ownership budget either.
    db = RecoveryLedger(db.path, max_instances=2, max_owned=2)
    other = db.register(TID, witness(2), 2)
    db.arm(rid, [0, 1])
    with pytest.raises(LedgerLimit):
        db.arm(other, [0])
    assert len(db.scan()) == 2


def test_scanning_is_bounded_and_includes_acknowledged_rows(tmp_path):
    db, rid = ledger(tmp_path)
    db.acknowledge(rid, db.arm(rid, range(8)))
    pieces = []
    after = None
    while page := db.scan(after=after, limit=3):
        assert len(page) <= 3
        pieces.extend(row.piece for row in page)
        after = (page[-1].record_id, page[-1].piece)
    assert pieces == list(range(8))
    with pytest.raises(ValueError):
        db.scan(limit=129)


def test_failure_before_commit_rolls_back_ack_but_keeps_ownership(tmp_path):
    db, rid = ledger(tmp_path)
    epochs = db.arm(rid, [2])

    def fail():
        raise RuntimeError("fault before commit")

    with pytest.raises(RuntimeError, match="fault before"):
        db.acknowledge(rid, epochs, before_commit=fail)
    assert db.scan()[0].pending


def test_failure_after_commit_still_leaves_recoverable_coverage(tmp_path):
    db, rid = ledger(tmp_path)
    epochs = db.arm(rid, [2])

    def fail():
        raise RuntimeError("fault after commit")

    with pytest.raises(RuntimeError, match="fault after"):
        db.acknowledge(rid, epochs, after_commit=fail)
    assert not db.scan()[0].pending
    assert db.scan()[0].piece == 2


def test_newer_schema_is_not_rewritten(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with pytest.raises(LedgerSchemaError):
        RecoveryLedger(path).scan()
    assert path.read_bytes() == before


def test_unknown_unversioned_database_is_not_adopted(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE user_data (value TEXT)")
    before = path.read_bytes()
    with pytest.raises(LedgerSchemaError):
        RecoveryLedger(path).scan()
    assert path.read_bytes() == before


def test_corrupt_record_fails_before_native_recovery(tmp_path):
    db, rid = ledger(tmp_path)
    db.arm(rid, [2])
    with closing(sqlite3.connect(db.path)) as connection, connection:
        connection.execute("UPDATE generations SET witness='not json'")
    with pytest.raises(LedgerError, match="corrupt"):
        db.scan()


def test_admission_batch_is_bounded_before_database_write(tmp_path):
    db, _ = ledger(tmp_path)
    with pytest.raises(ValueError):
        db.arm(witness().token, range(db.ARM_LIMIT + 1))
    assert db.scan() == []


def test_wal_reader_sees_coverage_while_acknowledgement_is_uncommitted(tmp_path):
    db, rid = ledger(tmp_path)
    epochs = db.arm(rid, [2])
    entered, release = Event(), Event()

    def barrier():
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test commit barrier timed out")

    with ThreadPoolExecutor(max_workers=1) as worker:
        acknowledgement = worker.submit(
            db.acknowledge, rid, epochs, before_commit=barrier
        )
        try:
            assert entered.wait(5)
            rows = db.scan()
            assert len(rows) == 1 and rows[0].pending
        finally:
            release.set()
        assert acknowledgement.result(timeout=5) == (2,)
    assert len(db.scan()) == 1 and not db.scan()[0].pending


def test_exhausted_epoch_does_not_partially_admit_other_pieces(tmp_path):
    db, rid = ledger(tmp_path)
    db.arm(rid, [2])
    with closing(sqlite3.connect(db.path)) as connection, connection:
        connection.execute("UPDATE owned SET epoch=?", (MAX_EPOCH,))
    with pytest.raises(LedgerError, match="epoch exhausted"):
        db.arm(rid, [0, 2])
    assert [row.piece for row in db.scan()] == [2]
