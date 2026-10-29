import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from deluge_stream.repository import (
    MAX_INTEGER,
    MIGRATIONS,
    Conflict,
    Repository,
    RepositoryError,
    SchemaError,
)

TID = "a" * 40
V2 = "b" * 64


@pytest.fixture
def repo(tmp_path):
    result = Repository(tmp_path / "policy.sqlite")
    result.initialize()
    return result


def register(repo, tid=TID, key="instance-1"):
    return repo.register(tid, key, "alice")


def test_ordinary_registration_and_reopen_do_not_infer_temporary(repo):
    row = register(repo)
    assert row["is_temporary"] == row["published"] == 0
    assert row["fifo_sequence"] is None
    reopened = Repository(repo.path)
    reopened.initialize()
    assert reopened.get(row["record_id"]) == row
    assert register(reopened) == row


def test_policy_transitions_allocate_new_fifo_only_on_explicit_demotion(repo):
    row = register(repo)
    rid = row["record_id"]
    first = repo.set_policy(rid, temporary=True)
    assert first["fifo_sequence"] == 1
    assert repo.set_policy(rid, temporary=True) == first
    second = register(repo, "c" * 40, "instance-2")
    assert repo.set_policy(second["record_id"], temporary=True)["fifo_sequence"] == 2
    permanent = repo.set_policy(rid, temporary=False)
    assert permanent["fifo_sequence"] is None
    assert repo.set_policy(rid, temporary=True)["fifo_sequence"] == 3


@pytest.mark.parametrize("temporary", [True, False])
@pytest.mark.parametrize("save", [True, False])
def test_existing_add_never_demotes_permanent_or_moves_fifo(repo, temporary, save):
    row = register(repo)
    rid = row["record_id"]
    before = repo.set_policy(rid, temporary=temporary)
    after = repo.repeat_add(rid, save_to_db=save)
    assert bool(after["is_temporary"]) == (temporary and not save)
    assert after["published"] == 1
    assert after["fifo_sequence"] == (before["fifo_sequence"] if not save else None)
    assert repo.repeat_add(rid, save_to_db=save) == after


def test_publication_is_independent_and_status_sync_preserves_policy(repo):
    rid = register(repo)["record_id"]
    row = repo.set_policy(rid, temporary=True, published=True)
    unpublished = repo.set_policy(rid, published=False)
    assert unpublished["fifo_sequence"] == row["fifo_sequence"]
    assert unpublished["is_temporary"] == 1
    assert register(repo) == unpublished


def test_revision_cas_rejects_late_callbacks(repo):
    row = register(repo)
    rid = row["record_id"]
    changed = repo.set_policy(rid, temporary=True, revision=0)
    with pytest.raises(Conflict):
        repo.set_policy(rid, temporary=False, revision=0)
    assert repo.get(rid) == changed


def test_aliases_resolve_same_instance_and_collision_rolls_back(repo):
    row = repo.register(TID, "instance-1", "alice", aliases=[V2])
    assert repo.resolve(V2) == row
    with pytest.raises(Conflict):
        repo.register("c" * 40, "instance-2", "alice", aliases=[V2])
    assert repo.resolve("c" * 40) is None
    assert repo.scan() == [row]


def test_readd_and_stale_forget_never_touch_new_instance(repo):
    old = register(repo)
    rid = old["record_id"]
    repo.set_policy(rid, temporary=True, published=True)
    repo.set_metadata(rid, {"title": "old"})
    repo.set_viewed(rid, 1, 12.5)
    with pytest.raises(Conflict):
        register(repo, key="instance-2")
    assert repo.forget(rid)
    new = register(repo, key="instance-2")
    assert new["record_id"] != rid
    assert new["is_temporary"] == new["published"] == 0
    assert repo.get_metadata(new["record_id"]) == {}
    assert repo.list_viewed(new["record_id"]) == []
    assert not repo.forget(rid)
    with pytest.raises(Conflict):
        repo.set_policy(rid, temporary=True)
    with pytest.raises(Conflict):
        repo.set_viewed(rid, 1, 100)
    assert repo.get(new["record_id"]) == new
    assert repo.set_policy(new["record_id"], temporary=True)["fifo_sequence"] == 2


def test_owner_change_requires_explicit_reconciliation(repo):
    register(repo)
    with pytest.raises(Conflict):
        repo.register(TID, "instance-1", "bob")


def test_metadata_is_persistent_and_bounded(repo):
    rid = register(repo)["record_id"]
    repo.set_metadata(rid, {"title": "Фильм", "data": '{"x":1}'})
    repo.set_metadata(rid, {"poster": "https://example.invalid/image"})
    assert Repository(repo.path).get_metadata(rid)["title"] == "Фильм"
    with pytest.raises(ValueError):
        repo.set_metadata(rid, {"data": "x" * 65536})
    with pytest.raises(ValueError):
        repo.set_metadata(rid, {"save_path": "/not/allowed"})
    assert repo.get_metadata(rid)["data"] == '{"x":1}'


def test_viewed_roundtrip_and_pagination(repo):
    rid = register(repo)["record_id"]
    for index in (1, 2, 3):
        repo.set_viewed(rid, index, 12.5)
    repo.set_viewed(rid, 2, 99)
    reopened = Repository(repo.path)
    assert reopened.list_viewed(rid, after=1, limit=1) == [
        {"file_id": 2, "timecode": 99.0}
    ]
    reopened.remove_viewed(rid, 1)
    reopened.remove_viewed(rid, 1)
    assert len(reopened.list_viewed(rid)) == 2


@pytest.mark.parametrize(
    "file_id,timecode",
    [
        (0, 0),
        (-1, 0),
        (True, 0),
        (1, True),
        (1, -1),
        (1, float("nan")),
        (1, float("inf")),
        (1, "12.5"),
    ],
)
def test_viewed_rejects_invalid_values_without_mutation(repo, file_id, timecode):
    rid = register(repo)["record_id"]
    with pytest.raises(ValueError):
        repo.set_viewed(rid, file_id, timecode)
    assert repo.list_viewed(rid) == []


@pytest.mark.parametrize("value", [True, 0, -1, 3.0, "3", None, MAX_INTEGER + 1])
def test_invalid_limit_does_not_change_settings(repo, value):
    repo.set_settings({"max_temporary_torrents": 5})
    with pytest.raises(ValueError):
        repo.set_settings({"max_temporary_torrents": value})
    assert repo.get_settings()["max_temporary_torrents"] == 5


def test_settings_reopen_and_unknown_key_rejection(repo):
    assert repo.get_settings()["max_temporary_torrents"] == 3
    repo.set_settings({"max_temporary_torrents": 7})
    with pytest.raises(ValueError):
        repo.set_settings({"max_temporary_torrents": 2, "ttl": 5})
    assert Repository(repo.path).get_settings()["max_temporary_torrents"] == 7


def test_migrate_v1_preserves_policy(tmp_path):
    path = tmp_path / "v1.sqlite"
    with closing(sqlite3.connect(path)) as db:
        for statement in MIGRATIONS[0]:
            db.execute(statement)
        db.execute("PRAGMA user_version=1")
        db.execute(
            "INSERT INTO torrent_policy "
            "(record_id,torrent_id,instance_key,owner,is_temporary,"
            "fifo_sequence,published) VALUES ('saved',?,'key','alice',1,42,1)",
            (TID,),
        )
        db.execute(
            "INSERT INTO torrent_metadata VALUES ('saved', '{\"title\":\"keep\"}')"
        )
        db.execute("UPDATE counters SET value=42")
        db.execute("INSERT INTO aliases VALUES (?, 'saved')", (TID,))
        db.commit()
    repo = Repository(path)
    repo.initialize()
    row = repo.get("saved")
    assert row["is_temporary"] == row["published"] == 1
    assert row["fifo_sequence"] == 42
    assert repo.get_metadata("saved") == {"title": "keep"}
    assert repo.resolve(TID) == row
    repo.set_viewed("saved", 1, 5)
    repo.initialize()
    assert repo.list_viewed("saved")[0]["timecode"] == 5
    assert repo.set_policy("saved", temporary=False)["fifo_sequence"] is None
    assert repo.set_policy("saved", temporary=True)["fifo_sequence"] == 43


def test_migration_failure_is_atomic(tmp_path, monkeypatch):
    import deluge_stream.repository as module

    repo = Repository(tmp_path / "broken-migration.sqlite")
    monkeypatch.setattr(module, "MIGRATIONS", (MIGRATIONS[0], ("INVALID SQL",)))
    with pytest.raises(sqlite3.OperationalError):
        repo.initialize()
    with closing(sqlite3.connect(repo.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            == []
        )


@pytest.mark.parametrize("kind", ["newer", "unversioned", "corrupt"])
def test_unknown_database_is_not_replaced(tmp_path, kind):
    path = tmp_path / "unknown.sqlite"
    if kind == "corrupt":
        path.write_bytes(b"not a database")
    else:
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE foreign_data (value TEXT)")
            db.execute("INSERT INTO foreign_data VALUES ('keep')")
            if kind == "newer":
                db.execute("PRAGMA user_version=999")
            db.commit()
    before = path.read_bytes()
    with pytest.raises((SchemaError, sqlite3.DatabaseError)):
        Repository(path).initialize()
    assert path.read_bytes() == before


def test_locked_database_has_bounded_timeout_and_no_partial_policy(repo):
    row = register(repo)
    with closing(sqlite3.connect(repo.path)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        impatient = Repository(repo.path, busy_timeout_ms=20)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            impatient.set_policy(row["record_id"], temporary=True)
        lock.rollback()
    assert repo.get(row["record_id"]) == row


def test_sequence_exhaustion_rolls_back(repo):
    rid = register(repo)["record_id"]
    with closing(sqlite3.connect(repo.path)) as db:
        db.execute("UPDATE counters SET value=?", (MAX_INTEGER,))
        db.commit()
    with pytest.raises(RepositoryError):
        repo.set_policy(rid, temporary=True, published=True)
    assert repo.get(rid)["published"] == repo.get(rid)["is_temporary"] == 0


def test_policy_fault_rolls_back_counter_journal_and_row(repo):
    rid = register(repo)["record_id"]
    with closing(sqlite3.connect(repo.path)) as db:
        db.execute(
            "CREATE TRIGGER fail_policy BEFORE INSERT ON operation_journal "
            "BEGIN SELECT RAISE(ABORT, 'fault'); END"
        )
        db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="fault"):
        repo.set_policy(rid, temporary=True)
    assert repo.get(rid)["is_temporary"] == 0
    with closing(sqlite3.connect(repo.path)) as db:
        assert db.execute("SELECT value FROM counters").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_policy")
        db.commit()
    assert repo.set_policy(rid, temporary=True)["fifo_sequence"] == 1


def test_completed_policy_journal_is_compacted_per_instance(repo):
    rid = register(repo)["record_id"]
    for _ in range(20):
        repo.set_policy(rid, temporary=True)
        repo.set_policy(rid, temporary=False)
    with closing(sqlite3.connect(repo.path)) as db:
        assert db.execute("SELECT count(*) FROM operation_journal").fetchone()[0] == 1


def test_policy_does_not_delete_any_payload(repo, tmp_path):
    payload = tmp_path / "downloads" / "movie"
    payload.parent.mkdir()
    payload.write_bytes(b"keep")
    rid = register(repo)["record_id"]
    repo.set_settings({"max_temporary_torrents": 1})
    repo.set_policy(rid, temporary=True)
    repo.forget(rid)
    assert payload.read_bytes() == b"keep"


def test_concurrent_policy_writers_allocate_distinct_fifo_keys(repo):
    rows = [
        register(repo, f"{index:040x}", f"instance-{index}") for index in range(1, 9)
    ]
    # Direct synchronous repository calls emulate competing DB writers. The
    # production dispatcher is stricter: one DB thread/connection at a time.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda row: repo.set_policy(row["record_id"], temporary=True), rows
            )
        )
    assert sorted(row["fifo_sequence"] for row in results) == list(range(1, 9))


def test_scan_is_bounded_and_stable_without_timestamp_ordering(repo):
    expected = [
        register(repo, f"{index:040x}", f"instance-{index}")["record_id"]
        for index in range(1, 6)
    ]
    found = []
    after = ""
    while page := repo.scan(after=after, limit=2):
        found.extend(row["record_id"] for row in page)
        after = page[-1]["record_id"]
    assert found == sorted(expected)
    with pytest.raises(ValueError):
        repo.scan(limit=129)
