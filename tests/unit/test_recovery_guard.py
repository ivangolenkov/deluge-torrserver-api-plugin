from deluge_stream.recovery_guard import PolicyStamp, admit_result


def test_old_result_cannot_touch_readded_hash():
    old, new = object(), object()
    policy = PolicyStamp((4,), False)
    assert admit_result(old, new, policy, policy, True) == "instance_changed"
    assert admit_result(old, None, policy, policy, True) == "instance_changed"


def test_policy_change_during_worker_requires_retry():
    instance = object()
    before = PolicyStamp((4, 4), False)
    after = PolicyStamp((0, 6), True)
    assert admit_result(instance, instance, before, after, True) == "policy_changed"


def test_unproven_persistent_identity_fails_closed():
    instance = object()
    policy = PolicyStamp((4,), False)
    assert (
        admit_result(instance, instance, policy, policy, False) == "identity_unproven"
    )
    assert admit_result(instance, instance, policy, policy, True) == "ready"
