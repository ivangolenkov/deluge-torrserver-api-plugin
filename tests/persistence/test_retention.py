import subprocess
import sys
from pathlib import Path


def test_coordinator_barriers_with_real_sqlite(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path("
            "'tests/persistence/retention_scenario.py', run_name='__main__')",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
