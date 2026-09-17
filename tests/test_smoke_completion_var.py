# -*- coding: utf-8 -*-

"""scripts/smoke.py names the completion variable the way typer does.

The release renames each PyInstaller artifact to phabfive-<os>-<arch> before
smoke testing it, and typer derives the completion environment variable from
whatever the binary is called. Getting that name wrong does not look like a
broken test: the binary never sees a completion instruction, falls through to
an ordinary run with no arguments, prints usage and exits 2, which reads
exactly like completion itself being broken.

That failure mode is only reachable from a release, so it cost two release
candidates to find. These tests move it into the normal suite.

The rule lives in typer.core:

    complete_var = f"_{prog_name}_COMPLETE".replace("-", "_").upper()

which is click's pre-8.2 rule: "-" becomes "_" and a "." is left alone. click
8.2 started mapping dots too, and following click instead of typer is what
broke the Windows executables in v0.10.0-rc.3 -- the .exe suffix is the only
dot among the release assets.
"""

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from smoke import completion_var  # noqa: E402


# Every asset name release.yml builds, plus the plain console script.
@pytest.mark.parametrize(
    "program, expected",
    [
        ("phabfive", "_PHABFIVE_COMPLETE"),
        ("phabfive-linux-amd64", "_PHABFIVE_LINUX_AMD64_COMPLETE"),
        ("phabfive-linux-arm64", "_PHABFIVE_LINUX_ARM64_COMPLETE"),
        ("phabfive-macos-amd64", "_PHABFIVE_MACOS_AMD64_COMPLETE"),
        ("phabfive-macos-arm64", "_PHABFIVE_MACOS_ARM64_COMPLETE"),
        # The dot survives. Mapping it to "_" is click's rule, not typer's.
        ("phabfive-windows-amd64.exe", "_PHABFIVE_WINDOWS_AMD64.EXE_COMPLETE"),
        ("phabfive-windows-arm64.exe", "_PHABFIVE_WINDOWS_ARM64.EXE_COMPLETE"),
    ],
)
def test_completion_var_matches_typer(program, expected):
    assert completion_var(program) == expected


def test_completion_var_agrees_with_typer_source():
    """Read the rule out of the installed typer rather than restating it.

    A typer release that changes how the variable is spelled should fail here,
    where the message says so, rather than in a release job where it shows up
    as completion looking broken on one platform.
    """
    import typer.core

    source = pathlib.Path(typer.core.__file__).read_text()
    match = re.search(r"complete_var = (f\".*?\"(?:\.\w+\([^)]*\))*)", source)
    assert match, "could not find typer's complete_var derivation"

    for program in ["phabfive", "phabfive-windows-amd64.exe", "phabfive-linux-arm64"]:
        prog_name = program  # noqa: F841 -- the name typer's expression reads
        assert completion_var(program) == eval(match.group(1))  # noqa: S307
