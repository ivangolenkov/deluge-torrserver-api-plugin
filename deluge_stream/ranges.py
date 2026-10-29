"""Single byte-range policy from SPEC §6.1; stop is exclusive."""

import re


class InvalidRange(ValueError):
    pass


class UnsatisfiableRange(ValueError):
    pass


def resolve_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if size < 0:
        raise ValueError("negative size")
    if value is None:
        return 0, size, False
    unit, sep, spec = value.partition("=")
    if not sep:
        raise InvalidRange("missing unit")
    if unit.strip() != "bytes":
        return 0, size, False
    parts = spec.split(",")
    for part in parts:
        if not re.fullmatch(r"\s*(\d+-\d*|-\d+)\s*", part):
            raise InvalidRange("invalid byte range")
    if len(parts) > 1:
        return 0, size, False
    left, right = spec.strip().split("-")
    if not left:
        suffix = int(right)
        if suffix == 0 or size == 0:
            raise UnsatisfiableRange("empty suffix")
        return max(0, size - suffix), size, True
    start = int(left)
    stop = int(right) + 1 if right else size
    if start >= size or stop <= start:
        raise UnsatisfiableRange("outside file")
    return start, min(stop, size), True
