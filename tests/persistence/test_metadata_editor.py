"""Metadata CAS uses the same row/transaction as all TorrServer writers."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from deluge_stream.repository import MAX_TEXT, MIGRATIONS, Conflict, Repository


@pytest.fixture
def stored(tmp_path):
    repo = Repository(tmp_path / "metadata.sqlite")
    repo.initialize()
    rid = repo.register("a" * 40, "instance", "alice")["record_id"]
    return repo, rid


def test_patch_roundtrip_reopen_and_policy_independence(stored):
    repo, rid = stored
    policy = repo.get(rid)
    before = repo.metadata_snapshot(rid)
    assert before["fields"] == {}
    fields = {
        "title": "Фильм 🎬",
        "data": "не JSON\r\n\t\x00",
        "poster": "",
        "category": "tv",
    }
    saved = repo.edit_metadata(rid, before["version"], fields)
    assert saved["fields"] == fields and saved["version"] != before["version"]
    updated = repo.edit_metadata(rid, saved["version"], {"title": ""})
    assert updated["fields"] == {**fields, "title": ""}
    reopened = Repository(repo.path)
    reopened.initialize()
    assert reopened.metadata_snapshot(rid) == updated
    assert repo.get(rid) == policy
    assert repo.edit_metadata(rid, updated["version"], {}) == updated


def test_two_writers_only_one_commits(stored):
    repo, rid = stored
    version = repo.metadata_snapshot(rid)["version"]

    def write(title):
        try:
            return repo.edit_metadata(rid, version, {"title": title})
        except Conflict as error:
            assert str(error) == "metadata_conflict"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("first", "second")))
    assert sum(result is not None for result in results) == 1
    assert repo.metadata_snapshot(rid) in results


def test_torrserver_set_and_aba_conflict(stored):
    repo, rid = stored
    repo.set_metadata(rid, {"title": "original"})
    version = repo.metadata_snapshot(rid)["version"]
    repo.set_metadata(rid, {"title": "changed"})
    repo.set_metadata(rid, {"title": "original"})
    with pytest.raises(Conflict, match="metadata_conflict"):
        repo.edit_metadata(rid, version, {"data": "stale"})
    assert repo.get_metadata(rid) == {"title": "original"}


def test_source_commit_updates_metadata_version(stored):
    repo, rid = stored
    snapshot = repo.metadata_snapshot(rid)
    token = repo.begin_source_add("alice", "a" * 40, "/isolated")
    repo.commit_source_add(
        token, rid, repo.get(rid)["revision"], True, {"data": "lampa"}
    )
    with pytest.raises(Conflict, match="metadata_conflict"):
        repo.edit_metadata(rid, snapshot["version"], {"title": "stale"})


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": "x"},
        {"title": 1},
        {"data": None},
        [],
        {"data": "я" * MAX_TEXT},
        {"data": "\ud800"},
    ],
)
def test_invalid_patch_never_changes_row(stored, changes):
    repo, rid = stored
    before = repo.metadata_snapshot(rid)
    with pytest.raises(ValueError, match="invalid_request"):
        repo.edit_metadata(rid, before["version"], changes)
    assert repo.metadata_snapshot(rid) == before


def test_combined_limit_and_rollback(stored):
    repo, rid = stored
    repo.set_metadata(rid, {"data": "a" * (MAX_TEXT - 20)})
    before = repo.metadata_snapshot(rid)
    with pytest.raises(ValueError, match="invalid_request"):
        repo.edit_metadata(rid, before["version"], {"title": "b" * 100})
    assert repo.metadata_snapshot(rid) == before
    with closing(sqlite3.connect(repo.path)) as db:
        db.execute(
            "CREATE TRIGGER fail_metadata BEFORE UPDATE ON torrent_metadata "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        repo.edit_metadata(rid, before["version"], {"data": "replacement"})
    assert repo.metadata_snapshot(rid) == before


def test_migration_preserves_existing_body_and_disable_resets(stored):
    repo, rid = stored
    # Recreate the previous schema, as an old installation would contain it.
    with closing(sqlite3.connect(repo.path)) as db:
        db.execute("ALTER TABLE torrent_metadata DROP COLUMN version")
        db.execute(
            "UPDATE torrent_metadata SET body=? WHERE record_id=?",
            ('{"data":"legacy\\r\\n"}', rid),
        )
        db.execute(f"PRAGMA user_version={len(MIGRATIONS) - 1}")
        db.commit()
    repo.initialize()
    assert repo.metadata_snapshot(rid)["fields"] == {"data": "legacy\r\n"}
    old = repo.metadata_snapshot(rid)
    repo.forget(rid)
    new = repo.register("a" * 40, "replacement", "alice")["record_id"]
    with pytest.raises(Conflict):
        repo.edit_metadata(rid, old["version"], {"title": "stale"})
    assert repo.metadata_snapshot(new)["fields"] == {}
