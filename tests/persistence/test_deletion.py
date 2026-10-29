import pytest

from deluge_stream.deletion import (
    build_deletion_plan,
    verify_absent,
    verify_plan_unchanged,
)
from deluge_stream.repository import Conflict, Repository

TID = "a" * 40


def test_plan_detects_replacement_and_checks_absence_without_deleting(tmp_path):
    payload = tmp_path / "payload"
    payload.write_bytes(b"payload")
    catalogue = ((TID, str(tmp_path), ("payload", ".hash.parts")),)
    plan = build_deletion_plan(TID, catalogue)
    assert verify_plan_unchanged(plan, catalogue)
    assert not verify_absent(plan)
    # Keep old inode alive, so the replacement cannot reuse that identity.
    payload.rename(tmp_path / "old")
    payload.write_bytes(b"replacement")
    with pytest.raises(ValueError, match="paths_changed"):
        verify_plan_unchanged(plan, catalogue)
    assert payload.read_bytes() == b"replacement"
    payload.unlink()
    assert verify_absent(plan)
    assert (tmp_path / "old").read_bytes() == b"payload"


def test_plan_rejects_shared_pad_or_partfile(tmp_path):
    for name in (".pad/1", ".hash.parts"):
        catalogue = (
            (TID, str(tmp_path), ("payload", name)),
            ("b" * 40, str(tmp_path), (name,)),
        )
        with pytest.raises(ValueError, match="shared_paths"):
            build_deletion_plan(TID, catalogue)


def test_symlink_parent_is_rejected_without_touching_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload").write_bytes(b"sentinel")
    root = tmp_path / "root"
    root.mkdir()
    catalogue = ((TID, str(root), ("nested/payload",)),)
    plan = build_deletion_plan(TID, catalogue)
    (root / "nested").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_paths"):
        verify_plan_unchanged(plan, catalogue)
    assert not verify_absent(plan)
    assert (outside / "payload").read_bytes() == b"sentinel"


@pytest.fixture
def removal(tmp_path):
    repo = Repository(tmp_path / "policy.sqlite")
    repo.initialize()
    row = repo.register(TID, "native-old", "alice")
    row = repo.set_policy(row["record_id"], temporary=True)
    plan = {"torrent_id": TID, "root": str(tmp_path), "files": [], "parents": {}}
    op = repo.prepare_removal(
        row["record_id"], row["revision"], row["fifo_sequence"], plan
    )
    return repo, row, op


def test_intent_is_durable_and_independent_of_reconciliation(removal):
    repo, row, op = removal
    repo.reconcile(())
    reopened = Repository(repo.path)
    reopened.initialize()
    [pending] = reopened.pending_removals()
    assert pending["operation_id"] == op
    assert pending["record_id"] == row["record_id"]
    assert pending["stage"] == "prepared"


def test_completion_cannot_delete_readded_instance(removal):
    repo, old, op = removal
    repo.removal_state(op, "submitting")
    repo.removal_state(op, "submitted")
    repo.reconcile(())
    new = repo.register(TID, "native-new", "alice")
    repo.removal_state(op, "completed")
    assert repo.get(new["record_id"]) == new
    with pytest.raises(Conflict):
        repo.get(old["record_id"])
    assert repo.pending_removals() == []


@pytest.mark.parametrize(
    "stage,expected",
    [("prepared", "aborted"), ("submitting", "manual"), ("submitted", "manual")],
)
def test_disable_preserves_ambiguous_native_removal_evidence(removal, stage, expected):
    repo, _, op = removal
    if stage != "prepared":
        repo.removal_state(op, "submitting")
    if stage == "submitted":
        repo.removal_state(op, "submitted")
    repo.reset_bindings()
    pending = repo.pending_removals()
    assert ([entry["stage"] for entry in pending] if pending else ["aborted"]) == [
        expected
    ]


def test_journal_rejects_skipped_stage_and_multiple_pending(removal):
    repo, row, op = removal
    with pytest.raises(Conflict):
        repo.removal_state(op, "completed")
    with pytest.raises(Conflict):
        repo.prepare_removal(
            row["record_id"], row["revision"], row["fifo_sequence"], {}
        )
    repo.removal_state(op, "aborted")
    with pytest.raises(Conflict):
        repo.removal_state(op, "submitting")
