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

import re
import subprocess
import sys
from importlib import resources

import click
import pytest
from typer.main import get_command

from phabfive.cli import _VALUELESS_GLOBAL_FLAGS, app

_ENTRYPOINT = (
    "import sys; sys.argv = ['phabfive', *sys.argv[1:]]; "
    "from phabfive.cli import cli_entrypoint; cli_entrypoint()"
)

# A fenced block introduced by ```bash
_BASH_BLOCK = re.compile(r"^```bash$(.*?)^```$", re.MULTILINE | re.DOTALL)

# A value rather than a subcommand: a monogram list such as T1,T2, a quoted
# string, or a <placeholder>
_NOT_A_SUBCOMMAND = re.compile(r"^([TKPR]\d+(,[TKPR]\d+)*|[<\"'$].*)$")


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


def _documented_commands():
    """Every "phabfive ..." command path the skill tells an agent to run.

    Deliberately conservative: lines with a pipe or a shell variable are
    skipped, and the path stops at the first option, monogram or placeholder.
    """
    for block in _BASH_BLOCK.findall(_skill_text()):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("phabfive ") or "|" in line or "$" in line:
                continue
            path = []
            for word in line.split()[1:]:
                if word.startswith("-") or _NOT_A_SUBCOMMAND.match(word):
                    break
                path.append(word)
            if path:
                yield line, tuple(path)


def test_the_skill_documents_commands():
    """Guard the guard: a broken extractor would make the next test vacuous."""
    assert len(set(_documented_commands())) > 10


@pytest.mark.parametrize(
    "line,path",
    sorted(set(_documented_commands())),
    ids=lambda value: " ".join(value) if isinstance(value, tuple) else None,
)
def test_documented_command_exists(line, path):
    """The skill goes stale silently; this is what notices."""
    command = get_command(app)
    ctx = click.Context(command, info_name="phabfive")

    for name in path:
        assert isinstance(command, click.Group), f"{line!r}: {name} is not a group"
        command = command.get_command(ctx, name)
        assert command is not None, f"{line!r}: no such command {name}"
        ctx = click.Context(command, parent=ctx, info_name=name)
