import subprocess
import sys
from pathlib import Path


def test_real_reactor_worker_lock_cancellation_and_shutdown(tmp_path):
    root = Path(__file__).resolve().parents[2]
    # Explicit source-level integration. Artifact lifecycle remains a separate
    # mandatory suite; this test does not claim installation into Deluge.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path('tests/persistence/worker_scenario.py', "
            "run_name='__main__')",
            str(tmp_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "drain/join passed" in result.stdout


def test_real_sqlite_service_commit_cancel_and_readd_barriers(tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; "
            "runpy.run_path('tests/persistence/service_scenario.py', "
            "run_name='__main__')",
            str(tmp_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "barriers and bounded admission passed" in result.stdout
