# -*- coding: utf-8 -*-

"""A command group without a subcommand must list its subcommands.

`phabfive paste` used to answer "Error: Missing command.", leaving the caller
to type `phabfive paste --help` to find out that `search`, `create`, `show`,
`edit` and `comment` exist. Bare `phabfive` already printed the full help, so
the groups now match it: help on stderr, exit 2.

`phabfive -v` is the same dead end for the root group, but by a different
route -- argv is non-empty there, so `no_args_is_help` never fires and the
root callback has to catch it.
"""

import subprocess
import sys

import pytest
from typer.testing import CliRunner

from phabfive.cli import app

runner = CliRunner()

# Invoked out of process by test_help_goes_to_stderr; sys.argv[1:] is the
# command line under test.
_ENTRYPOINT = (
    "import sys; sys.argv = ['phabfive', *sys.argv[1:]]; "
    "from phabfive.cli import cli_entrypoint; cli_entrypoint()"
)

# Every group reachable from the root app, except repl, which is meant to run
# without a subcommand. "-v" stands for any global option given on its own.
GROUPS = [
    [],
    ["-v"],
    ["--format=json"],
    ["cache"],
    ["passphrase"],
    ["diffusion"],
    ["paste"],
    ["user"],
    ["maniphest"],
    ["diffusion", "repo"],
    ["diffusion", "uri"],
    ["diffusion", "branch"],
]


def _output(result):
    """Combined stdout/stderr regardless of click version."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass
    return output


@pytest.mark.parametrize("args", GROUPS, ids=lambda a: " ".join(a) or "(none)")
class TestGroupWithoutSubcommand:
    def test_lists_subcommands(self, args):
        result = runner.invoke(app, args)

        assert "Commands" in _output(result)

    def test_does_not_say_missing_command(self, args):
        result = runner.invoke(app, args)

        assert "Missing command" not in _output(result)

    def test_exits_two(self, args):
        result = runner.invoke(app, args)

        assert result.exit_code == 2


def test_paste_lists_its_own_commands():
    """Guard against help that renders but has lost its command list."""
    result = runner.invoke(app, ["paste"])

    output = _output(result)
    for command in ("search", "create", "show", "edit", "comment"):
        assert command in output


def test_root_lists_its_own_commands():
    result = runner.invoke(app, ["-v"])

    output = _output(result)
    for command in ("cache", "passphrase", "diffusion", "paste", "user", "maniphest"):
        assert command in output


@pytest.mark.parametrize("args", [["paste"], ["-v"]])
def test_help_goes_to_stderr(args):
    """stdout stays empty so `phabfive paste > out` does not capture help.

    Run out of process: CliRunner imports typer before phabfive.cli can set
    TYPER_USE_RICH=0, so under the runner typer's rich error path writes the
    help to stdout instead of click's stderr. Only the real entrypoint has
    the import order the installed console script has.
    """
    result = subprocess.run(
        [sys.executable, "-c", _ENTRYPOINT, *args],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Commands" not in result.stdout
    assert "Commands" in result.stderr
