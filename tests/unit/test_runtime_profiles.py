import pytest

from deluge_stream.runtime_profiles import RUNTIME_PROFILES, supported_runtime
from tools.version import egg_name, python_requires, python_tag


@pytest.mark.parametrize("python", ["3.11", "3.14"])
def test_exact_native_profiles_and_python_specific_artifacts(python):
    profile = RUNTIME_PROFILES[python]
    assert supported_runtime(
        "Linux", python, "2.2.0", profile["libtorrent"], profile["Twisted"], (3, 40, 1)
    )
    assert egg_name(python).endswith(f"-py{python}.egg")
    assert f">={python}" in python_requires(python)
    assert not supported_runtime(
        "Darwin", python, "2.2.0", profile["libtorrent"], profile["Twisted"], (3, 40, 1)
    )
    assert not supported_runtime(
        "Linux", python, "2.2.0", "2.1.0.0", profile["Twisted"], (3, 40, 1)
    )


@pytest.mark.parametrize("python", ["3.12", "3.13", "3.15"])
def test_untested_minors_are_not_silently_accepted(python):
    with pytest.raises(ValueError, match="unsupported Python"):
        python_tag(python)
    assert not supported_runtime(
        "Linux", python, "2.2.0", "2.0.14.0", "26.10.0", (3, 50, 0)
    )
