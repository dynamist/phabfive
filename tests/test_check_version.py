# -*- coding: utf-8 -*-

"""scripts/check_version.py refuses a tag that disagrees with pyproject.toml.

Every release artifact is named after pyproject.toml while the release itself is
named after the tag, so the two disagreeing publishes a release full of
artifacts for a different version. v0.10.0-rc.1 did exactly that, over a
pyproject.toml that still read 0.10.0-dev.0, and nothing in the pipeline said a
word about it.

Keeping the two in step is otherwise only a convention -- they are edited by
hand, in the same commit when someone remembers. These tests are what make the
release refuse instead.
"""

import pathlib
import sys

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

pytestmark = pytest.mark.skipif(
    tomllib is None, reason="reading pyproject.toml needs tomllib (Python 3.11+)"
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from check_version import main  # noqa: E402


def pyproject(tmp_path, version):
    """A minimal pyproject.toml declaring one version."""
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "phabfive"\nversion = "{version}"\n')
    return str(path)


@pytest.mark.parametrize(
    "tag, version",
    [
        ("v0.11.0", "0.11.0"),
        # The tag spelling and the canonical PEP 440 spelling of a release
        # candidate differ, and hatchling builds the canonical one.
        ("v0.11.0-rc.1", "0.11.0rc1"),
        ("v0.11.0-rc.1", "0.11.0-rc.1"),
        ("v1.0.0-rc.12", "1.0.0rc12"),
        # The workflow only triggers on "v*", but the prefix is not required.
        ("0.11.0", "0.11.0"),
    ],
)
def test_agreeing_versions_pass(tmp_path, tag, version):
    assert main(["check_version.py", tag, pyproject(tmp_path, version)]) == 0


@pytest.mark.parametrize(
    "tag, version",
    [
        # The v0.10.0-rc.1 case: a candidate tagged over an unbumped dev version.
        ("v0.10.0-rc.1", "0.10.0-dev.0"),
        ("v0.10.0", "0.10.0.dev0"),
        # A bump that went one release too far, or not far enough.
        ("v0.11.0", "0.12.0"),
        ("v0.11.0", "0.10.0"),
        # Right release, wrong candidate.
        ("v0.11.0-rc.2", "0.11.0rc1"),
        # A final tag over a candidate version, which is the bump that is
        # easiest to forget: the tag moves and the version does not.
        ("v0.11.0", "0.11.0rc1"),
    ],
)
def test_disagreeing_versions_fail(tmp_path, tag, version):
    assert main(["check_version.py", tag, pyproject(tmp_path, version)]) == 1


@pytest.mark.parametrize("version", ["0.11.0.dev0", "0.11.0-dev.0", "0.12.0dev1"])
def test_dev_versions_are_refused_even_when_the_tag_agrees(tmp_path, version):
    """Equality alone would let a dev release through.

    Tagging v0.11.0-dev.0 over a 0.11.0.dev0 pyproject.toml is self-consistent
    and still not something to publish: it means the tag was pushed before the
    release bump rather than after it.
    """
    assert main(["check_version.py", f"v{version}", pyproject(tmp_path, version)]) == 1


def test_a_pyproject_without_a_version_is_an_error(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "phabfive"\n')

    with pytest.raises(SystemExit):
        main(["check_version.py", "v0.11.0", str(path)])


def test_the_argument_count_is_checked():
    with pytest.raises(SystemExit):
        main(["check_version.py"])
