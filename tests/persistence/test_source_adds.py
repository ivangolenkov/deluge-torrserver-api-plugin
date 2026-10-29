import sqlite3
from contextlib import closing

import pytest

from deluge_stream.repository import Conflict, Repository, RepositoryError

TID = "e" * 40


@pytest.fixture
def source(tmp_path):
    repo = Repository(tmp_path / "policy.sqlite")
    repo.initialize()
    row = repo.register(TID, "new-native", "alice")
    token = repo.begin_source_add("alice", TID, str(tmp_path / "instance"))
    return repo, row, token


def stage(repo, token):
    with closing(sqlite3.connect(repo.path)) as db:
        return db.execute(
            "SELECT stage FROM source_adds WHERE token=?", (token,)
        ).fetchone()[0]


@pytest.mark.parametrize("save", [False, True])
def test_add_policy_metadata_and_intent_commit_atomically(source, save):
    repo, row, token = source
    result = repo.commit_source_add(
        token, row["record_id"], 0, save, {"title": "fixture"}
    )
    assert result["published"] and bool(result["is_temporary"]) is not save
    assert repo.get_metadata(row["record_id"]) == {"title": "fixture"}
    assert stage(repo, token) == "done"
    repo.finish_source_add(token, "failed")  # A disconnected reply cannot undo commit.
    assert stage(repo, token) == "done"
    with pytest.raises(Conflict):
        repo.commit_source_add(token, row["record_id"], result["revision"], save, {})


@pytest.mark.parametrize("owner,alias", [("bob", TID), ("alice", "f" * 40)])
def test_wrong_owner_or_alias_cannot_publish_or_demote_existing(source, owner, alias):
    repo, row, _ = source
    token = repo.begin_source_add(owner, alias, "/fixture/new")
    with pytest.raises(Conflict):
        repo.commit_source_add(token, row["record_id"], 0, False, {})
    assert repo.get(row["record_id"]) == row


def test_oversized_metadata_rolls_back_entire_add_commit(source):
    repo, row, token = source
    with pytest.raises(ValueError):
        repo.commit_source_add(token, row["record_id"], 0, False, {"data": "x" * 65536})
    assert repo.get(row["record_id"]) == row
    assert stage(repo, token) == "pending"


def test_restart_abandons_uncommitted_add_without_inferring_policy(source):
    repo, row, token = source
    restored = Repository(repo.path)
    restored.initialize()
    restored.recover_source_adds()
    assert restored.get(row["record_id"]) == row
    assert stage(repo, token) == "abandoned"
    with pytest.raises(Conflict):
        restored.commit_source_add(token, row["record_id"], 0, False, {})


def test_add_intents_are_bounded_and_explicit_disable_revokes_them(source):
    repo, _, _ = source
    for _ in range(31):
        repo.begin_source_add("alice", TID, "/fixture/new")
    with pytest.raises(RepositoryError):
        repo.begin_source_add("alice", TID, "/fixture/new")
    repo.reset_bindings()
    with closing(sqlite3.connect(repo.path)) as db:
        assert db.execute("SELECT count(*) FROM source_adds").fetchone()[0] == 0
