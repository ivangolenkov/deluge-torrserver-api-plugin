import importlib.util
from pathlib import Path


def test_broken_link_is_error(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "check_docs", Path(__file__).resolve().parents[2] / "tools/check_docs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "README.md").write_text("# Test\n\n[bad](missing.md)\n")
    count, errors = module.check(tmp_path)
    assert count == 1
    assert len(errors) == 1
    (tmp_path / "missing.md").write_text("# Target\n")
    assert module.check(tmp_path) == (2, [])
