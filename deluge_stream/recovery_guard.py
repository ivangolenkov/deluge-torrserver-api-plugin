"""Pure admission guard for phase-0 worker-result experiments, not live QoS."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyStamp:
    file_priorities: tuple[int, ...]
    first_last: bool


def admit_result(
    expected_instance, current_instance, expected_policy, current_policy, witness_valid
):
    if current_instance is not expected_instance or current_instance is None:
        return "instance_changed"
    if not witness_valid:
        return "identity_unproven"
    if expected_policy != current_policy:
        return "policy_changed"
    return "ready"
