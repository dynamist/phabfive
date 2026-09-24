# -*- coding: utf-8 -*-

"""`phabfive --skill` prints the agent skill file and exits.

An AI agent that meets phabfive for the first time reads --help, so --help
points it here. The flag has to be eager: the root callback answers a bare
invocation with help on stderr and exit 2, which would otherwise win.

The skill is shipped as package data, and the wheel is not the only artifact --
the release also builds one-file PyInstaller executables, which collect data
files only when asked. test_skill_is_package_data guards that path from the
resource side, independently of the CLI.
"""

import subprocess
import sys
from importlib import resources

from phabfive.cli import _VALUELESS_GLOBAL_FLAGS

_ENTRYPOINT = (
    "import sys; sys.argv = ['phabfive', *sys.argv[1:]]; "
    "from phabfive.cli import cli_entrypoint; cli_entrypoint()"
)


def _skill_text() -> str:
    return resources.files("phabfive").joinpath("SKILL.md").read_text(encoding="utf-8")


def _run(*args):
    return subprocess.run(
        [sys.executable, "-c", _ENTRYPOINT, *args],
        capture_output=True,
        text=True,
    )


def test_skill_is_package_data():
    """Readable as a resource, so a frozen build can find it too."""
    assert _skill_text().startswith("---\n")


def test_prints_the_skill():
    result = _run("--skill")

    assert result.returncode == 0
    assert result.stdout.startswith("---\n")
    assert "name: phabfive" in result.stdout


def test_the_skill_encodes_on_a_windows_console():
    """`phabfive --skill` prints the file to stdout, and on Windows that is a
    cp1252 stream - so a character cp1252 cannot encode is a UnicodeEncodeError
    and a non-zero exit, on that platform only.

    An em dash survives (cp1252 has one); an arrow does not, which is how a
    `→` in a dry-run example took out every Windows job and cancelled two
    macOS ones with it.
    """
    text = _skill_text()

    try:
        text.encode("cp1252")
    except UnicodeEncodeError as e:
        raise AssertionError(
            f"SKILL.md holds {text[e.start : e.end]!r}, which a Windows console "
            "cannot print; use an ASCII spelling"
        ) from None


def test_writes_nothing_to_stderr():
    """The output is meant to be redirected straight into a SKILL.md."""
    result = _run("--skill")

    assert result.stderr == ""


def test_output_is_the_file_verbatim():
    result = _run("--skill")

    assert result.stdout == _skill_text()


def test_beats_the_bare_invocation_help():
    """Without is_eager the root callback would answer with help and exit 2."""
    result = _run("--skill")

    assert result.returncode == 0
    assert "Commands" not in result.stdout


def test_flag_takes_no_value():
    """Otherwise monogram preprocessing swallows the next argument."""
    assert "--skill" in _VALUELESS_GLOBAL_FLAGS


# The command lines in the skill are resolved against the real application
# by tests/test_skill_commands.py, which walks every fenced block rather than
# the ```bash ones alone, checks the options as well as the command path, and
# checks that every repository path a line names exists. It lives in its own
# file because it is a gate on the *content* of the skill, while this one is
# a gate on `--skill` the flag.
