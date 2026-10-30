import os
from types import SimpleNamespace

import pytest
from twisted.internet import defer
from twisted.python.failure import Failure

from deluge_stream import direct_reader as module
from deluge_stream.config import DEFAULTS
from deluge_stream.layout import FileSpan


def test_safe_open_range_and_in_place_content_edit(tmp_path):
    path = tmp_path / "payload"
    path.write_bytes(b"abcdefgh")
    file = FileSpan(0, "payload", 31, 8)
    identity = module.identity(str(tmp_path), file)
    assert module.read_block(str(tmp_path), file, identity, 2, 4) == b"cdef"
    path.write_bytes(b"ABCDEFGH")
    assert module.identity(str(tmp_path), file) == identity
    assert module.read_block(str(tmp_path), file, identity, 2, 4) == b"CDEF"


def test_replacement_is_not_an_in_place_edit(tmp_path):
    path = tmp_path / "payload"
    path.write_bytes(b"old")
    file = FileSpan(0, "payload", 0, 3)
    identity = module.identity(str(tmp_path), file)
    path.rename(tmp_path / "retired")
    path.write_bytes(b"new")
    with pytest.raises(RuntimeError, match="operation_conflict"):
        module.read_block(str(tmp_path), file, identity, 0, 3)


@pytest.mark.parametrize("relative", ["../foreign", "/foreign", "sub/link", "link"])
def test_unsafe_paths_and_symlink_components(tmp_path, relative):
    (tmp_path / "foreign").write_bytes(b"abc")
    (tmp_path / "link").symlink_to(tmp_path / "foreign")
    (tmp_path / "sub").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_paths"):
        module.identity(str(tmp_path), FileSpan(0, relative, 0, 3))


def test_root_symlink_and_fifo_are_not_payload(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_paths"):
        module.identity(str(tmp_path / "link"), FileSpan(0, "payload", 0, 1))
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(RuntimeError, match="storage_error"):
        module.identity(str(tmp_path), FileSpan(0, "pipe", 0, 1))


def test_truncation_and_short_read_fail_without_descriptor_leak(tmp_path, monkeypatch):
    path = tmp_path / "payload"
    path.write_bytes(b"data")
    file = FileSpan(0, "payload", 0, 4)
    identity = module.identity(str(tmp_path), file)
    baseline = len(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(module.os, "pread", lambda *_: b"da")
    with pytest.raises(RuntimeError, match="storage_error"):
        module.read_block(str(tmp_path), file, identity, 0, 4)
    assert len(os.listdir("/proc/self/fd")) == baseline
    path.write_bytes(b"d")
    with pytest.raises(RuntimeError, match="storage_error"):
        module.read_block(str(tmp_path), file, identity, 0, 4)


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(
        module,
        "ThreadPool",
        lambda **_: SimpleNamespace(start=lambda: None, stop=lambda: None),
    )
    monkeypatch.setattr(module.threads, "deferToThread", lambda fn: defer.succeed(fn()))
    calls = []

    def submit(reactor, pool, operation):
        result = defer.Deferred()
        calls.append((operation, result))
        return result

    monkeypatch.setattr(module.threads, "deferToThreadPool", submit)
    session = SimpleNamespace(file=object(), closed=False)

    def check(current):
        if current.closed:
            raise RuntimeError("operation_conflict")

    engine = SimpleNamespace(
        policy=SimpleNamespace(config=dict(DEFAULTS)),
        reserved=0,
        reactor=object(),
        adapter=SimpleNamespace(check=check),
    )
    reader = module.DirectReader(engine)
    yield reader, engine, session, calls
    for operation, result in calls:
        if not result.called:
            result.callback(operation())
    assert reader.close().called
    assert reader.pending == engine.reserved == 0


@pytest.mark.parametrize("issued", [False, True])
def test_cancel_holds_capacity_and_charge_until_actual_completion(worker, issued):
    reader, engine, session, calls = worker
    ran = []
    result = reader.submit(
        session, lambda: ran.append(True) or b"data", charge=8, start=0
    )
    result.addErrback(lambda failure: None)
    operation, completion = calls[0]
    value = operation() if issued else None
    result.cancel()
    assert engine.reserved == 8 and reader.pending == 1
    closed = reader.close()
    assert not closed.called
    completion.callback(value if issued else operation())
    assert bool(ran) == issued
    assert closed.called and reader.pending == engine.reserved == 0


def test_chunk_keeps_charge_and_invalidated_callback_discards_bytes(worker):
    reader, engine, session, calls = worker
    values = []
    reader.submit(session, lambda: b"data", charge=8, start=31).addBoth(values.append)
    calls[0][1].callback(calls[0][0]())
    chunk = values.pop()
    assert chunk.start == 31 and chunk.data == b"data" and engine.reserved == 8
    chunk.release()
    chunk.release()
    reader.submit(session, lambda: b"old", charge=8, start=31).addBoth(values.append)
    session.closed = True
    calls[1][1].callback(calls[1][0]())
    assert isinstance(values[0], Failure)
    assert str(values[0].value) == "operation_conflict"


def test_admission_is_bounded(worker):
    reader, engine, session, calls = worker
    for _ in range(reader.CAPACITY):
        reader.submit(session, lambda: b"", charge=0).addErrback(lambda _: None)
    failures = []
    reader.submit(session, lambda: None).addErrback(failures.append)
    assert str(failures[0].value) == "transfer_limit"
    for operation, completion in calls:
        completion.callback(operation())


def test_budget_and_worker_failure_release_accounting(worker):
    reader, engine, session, calls = worker
    failures = []
    budget = engine.policy.config["payload_buffer_budget_mib"] * 1024**2
    reader.submit(session, lambda: None, charge=budget + 1).addErrback(failures.append)
    assert str(failures.pop().value) == "buffer_limit"
    assert not calls and engine.reserved == 0
    reader.submit(session, lambda: None, charge=8, start=0).addErrback(failures.append)
    calls[0][1].errback(RuntimeError("storage_error"))
    assert str(failures.pop().value) == "storage_error"
    assert reader.pending == engine.reserved == 0


def test_file_changed_during_worker_job_rejects_delivery(worker):
    reader, engine, session, calls = worker
    failures = []
    reader.submit(session, lambda: b"old", charge=8, start=0).addErrback(
        failures.append
    )
    session.file = object()
    calls[0][1].callback(calls[0][0]())
    assert str(failures.pop().value) == "operation_conflict"
    assert reader.pending == engine.reserved == 0
