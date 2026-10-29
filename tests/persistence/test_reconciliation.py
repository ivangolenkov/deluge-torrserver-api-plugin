import pytest

from deluge_stream.repository import Conflict, Repository


def entry(tid="a" * 40, key="key1", owner="alice", fresh=False):
    return (tid, key, owner, (), fresh)


def test_daemon_restore_keeps_record_policy_metadata_and_viewed(tmp_path):
    repo = Repository(tmp_path / "policy.sqlite")
    repo.initialize()
    tid = "a" * 40
    rid = repo.reconcile((entry(),))[tid]["record_id"]
    row = repo.set_policy(rid, temporary=True, published=True)
    repo.set_metadata(rid, {"title": "keep"})
    repo.set_viewed(rid, 1, 12.5)
    reopened = Repository(repo.path)
    reopened.initialize()
    restored = reopened.reconcile((entry(key="restored-live-object"),))[tid]
    assert restored["record_id"] == rid
    assert restored["fifo_sequence"] == row["fifo_sequence"]
    assert restored["is_temporary"] == restored["published"] == 1
    assert reopened.get_metadata(rid) == {"title": "keep"}
    assert reopened.list_viewed(rid)[0]["timecode"] == 12.5


def test_explicit_disable_revokes_bindings_not_settings_or_counter(tmp_path):
    repo = Repository(tmp_path / "policy.sqlite")
    repo.initialize()
    tid = "a" * 40
    old = repo.reconcile((entry(),))[tid]["record_id"]
    repo.set_policy(old, temporary=True, published=True)
    repo.set_viewed(old, 1, 1)
    repo.set_settings({"max_temporary_torrents": 5})
    repo.reset_bindings()
    new = repo.reconcile((entry(key="new"),))[tid]
    assert new["record_id"] != old
    assert new["is_temporary"] == new["published"] == 0
    assert repo.get_settings()["max_temporary_torrents"] == 5
    assert repo.set_policy(new["record_id"], temporary=True)["fifo_sequence"] == 2
    with pytest.raises(Conflict):
        repo.set_policy(old, temporary=True)


@pytest.mark.parametrize(
    "replacement", [entry(key="key2", fresh=True), entry(key="key2", owner="bob")]
)
def test_new_add_or_owner_change_never_inherits_old_record(tmp_path, replacement):
    repo = Repository(tmp_path / "policy.sqlite")
    repo.initialize()
    tid = "a" * 40
    old = repo.reconcile((entry(),))[tid]["record_id"]
    repo.set_policy(old, temporary=True, published=True)
    new = repo.reconcile((replacement,))[tid]
    assert new["record_id"] != old and not new["is_temporary"]
    assert repo.resolve(tid)["record_id"] == new["record_id"]
    assert repo.reconcile(()) == {}
    assert repo.resolve(tid) is None


def test_dirty_run_requires_confirmation_and_clean_stop_does_not_clear_it(tmp_path):
    repo = Repository(tmp_path / "policy.sqlite")
    repo.initialize()
    assert repo.begin_run() is False
    # Reopening without finish_run models a process crash after the durable dirty mark.
    reopened = Repository(repo.path)
    assert reopened.begin_run() is True
    reopened.finish_run()
    assert reopened.begin_run() is True  # cannot bypass by a subsequent clean restart
    reopened.confirm_recovery()
    reopened.finish_run()
    assert reopened.begin_run() is False
    reopened.reset_bindings()
    assert reopened.begin_run() is False
