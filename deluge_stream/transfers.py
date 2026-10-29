"""Bounded reactor-only leases. No HTTP semantics or native QoS in this model."""

import uuid
from collections import Counter

KINDS = {"playback", "preload", "file", "zip", "ticket"}
LIMITS = {
    "playback": "max_playback_requests",
    "preload": "max_preload_jobs",
    "file": "max_file_downloads",
    "zip": "max_zip_downloads",
    "ticket": "max_pending_download_tickets",
}
TIMEOUTS = {
    "playback": "client_idle_timeout_seconds",
    "preload": "preload_timeout_seconds",
    "file": "client_idle_timeout_seconds",
    "zip": "client_idle_timeout_seconds",
    "ticket": "download_ticket_start_ttl_seconds",
}


class Lease:
    def __init__(self, registry, record_id, kind, cancel):
        self.registry = registry
        self.record_id = record_id
        self.kind = kind
        self.cancel = cancel
        self.token = str(uuid.uuid4())
        self.closed = False
        self.timer = None
        self.issued = False

    def release(self):
        return self.registry.release(self)

    def progress(self, byte_count):
        if type(byte_count) is not int or byte_count <= 0 or self.kind == "ticket":
            raise ValueError("invalid_progress")
        if self.closed:
            return False
        self.registry._renew(self)
        return True

    def issue(self):
        """Start the ticket's TTL once, after bounded readiness preflight."""
        if self.closed or self.kind != "ticket" or self.issued:
            raise ValueError("invalid_reservation")
        self.issued = True
        self.registry._renew(self)


class TransferRegistry:
    CAPACITY = 128

    def __init__(self, clock, config, changed):
        self.clock = clock
        self.config = config
        self.changed = changed
        self.leases = {}
        self.debounce = {}
        self.blocked = set()
        self.closed = False

    def acquire(self, record_id, kind, cancel=None):
        if kind not in KINDS:
            raise ValueError("invalid_transfer_kind")
        if self.closed or record_id in self.blocked:
            raise RuntimeError("operation_conflict")
        counts = Counter(lease.kind for lease in self.leases.values())
        if (
            len(self.leases) >= self.CAPACITY
            or counts[kind] >= self.config()[LIMITS[kind]]
        ):
            raise RuntimeError("transfer_limit")
        lease = Lease(self, record_id, kind, cancel)
        self.leases[lease.token] = lease
        if kind == "playback":
            self._clear_debounce(record_id)
        self._renew(lease)
        self.changed()
        return lease

    def activate(self, lease, kind):
        if kind not in {"file", "zip"} or lease.closed or lease.kind != "ticket":
            raise ValueError("invalid_reservation")
        if (
            self.closed
            or lease.record_id in self.blocked
            or self.leases.get(lease.token) is not lease
        ):
            raise RuntimeError("operation_conflict")
        count = sum(item.kind == kind for item in self.leases.values())
        if count >= self.config()[LIMITS[kind]]:
            raise RuntimeError("transfer_limit")
        lease.kind = kind
        self._renew(lease)
        self.changed()
        return lease

    def _renew(self, lease):
        if lease.timer is not None and lease.timer.active():
            lease.timer.cancel()
        lease.timer = self.clock.callLater(
            self.config()[TIMEOUTS[lease.kind]], self._expire, lease
        )

    def _expire(self, lease):
        self.release(lease, reason="expired")

    def release(self, lease, *, reason=None):
        if lease.closed or self.leases.get(lease.token) is not lease:
            return False
        lease.closed = True
        del self.leases[lease.token]
        if lease.timer is not None and lease.timer.active():
            lease.timer.cancel()
        if lease.kind == "playback" and not self.closed and reason != "retired":
            if not any(
                item.record_id == lease.record_id and item.kind == "playback"
                for item in self.leases.values()
            ):
                self._clear_debounce(lease.record_id)
                version = str(uuid.uuid4())
                timer = self.clock.callLater(
                    self.config()["debounce_seconds"],
                    self._debounced,
                    lease.record_id,
                    version,
                )
                self.debounce[lease.record_id] = (version, timer)
        if reason is not None and lease.cancel is not None:
            callback, lease.cancel = lease.cancel, None
            try:
                callback(reason)
            except Exception:
                # Cleanup/refcounts cannot depend on an HTTP callback succeeding.
                pass
        self.changed()
        return True

    def _clear_debounce(self, record_id):
        entry = self.debounce.pop(record_id, None)
        if entry is not None and entry[1].active():
            entry[1].cancel()

    def _debounced(self, record_id, version):
        entry = self.debounce.get(record_id)
        if entry is not None and entry[0] == version:
            del self.debounce[record_id]
            self.changed()

    def status(self, record_id):
        counts = Counter(
            lease.kind for lease in self.leases.values() if lease.record_id == record_id
        )
        reasons = sorted(counts)
        if record_id in self.debounce:
            reasons.append("debounce")
        return {
            "active_stream_requests": counts["playback"],
            "preload_jobs": counts["preload"],
            "file_downloads": counts["file"],
            "zip_downloads": counts["zip"],
            "ticket_reservations": counts["ticket"],
            "protection_reasons": reasons,
            "cleanup_protected": bool(reasons),
            "streaming_protected": bool(
                counts["playback"] or record_id in self.debounce
            ),
        }

    def retire(self, record_id):
        self._clear_debounce(record_id)
        for lease in tuple(self.leases.values()):
            if lease.record_id == record_id:
                self.release(lease, reason="retired")

    def reconcile(self, record_ids):
        owned = {
            lease.record_id for lease in self.leases.values()
        } | self.debounce.keys()
        for record_id in owned - set(record_ids):
            self.retire(record_id)

    def close(self):
        self.closed = True
        for rid in tuple(self.debounce):
            self._clear_debounce(rid)
        for lease in tuple(self.leases.values()):
            self.release(lease, reason="shutdown")
        self.blocked.clear()
