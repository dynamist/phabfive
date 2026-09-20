# -*- coding: utf-8 -*-

"""Every command group's help ends with the agent resources block.

The block is what tells an AI agent that `phabfive --skill` exists, so it has
to survive on the help paths an agent actually hits: bare `phabfive`, and a
bare group such as `phabfive maniphest`, which prints help to stderr. Leaf
commands are left alone -- by then the agent has found its command.

The indentation is the fragile part: click's default format_epilog runs the
epilog through write_text, which rewraps it and flattens the two-level indent.
AgentFooterGroup writes the lines itself.

Run out of process for the same reason as test_group_no_args_help: CliRunner
imports typer before phabfive.cli can set TYPER_USE_RICH=0, so under the runner
typer renders help with rich, which never calls format_epilog. Only the real
entrypoint has the import order the installed console script has.
"""

import subprocess
import sys

import pytest

from phabfive.cli.agents import AGENT_HELP_FOOTER

_ENTRYPOINT = (
    "import sys; sys.argv = ['phabfive', *sys.argv[1:]]; "
    "from phabfive.cli import cli_entrypoint; cli_entrypoint()"
)

# The line whose leading spaces click's rewrapping would eat
_INDENTED_LINE = "    SKIP if a phabfive skill is already in your context."

GROUPS = [
    [],
    ["cache"],
    ["passphrase"],
    ["diffusion"],
    ["paste"],
    ["user"],
    ["maniphest"],
    ["diffusion", "repo"],
    ["diffusion", "uri"],
]

LEAVES = [
    ["maniphest", "show"],
    ["maniphest", "search"],
    ["paste", "create"],
    ["passphrase", "show"],
    ["edit"],
]


def _run(*args):
    result = subprocess.run(
        [sys.executable, "-c", _ENTRYPOINT, *args],
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


@pytest.mark.parametrize("args", GROUPS, ids=lambda a: " ".join(a) or "(none)")
class TestGroupHelp:
    def test_has_the_footer(self, args):
        assert "Are you an AI?" in _run(*args, "--help")

    def test_mentions_the_skill_flag(self, args):
        assert "phabfive --skill" in _run(*args, "--help")

    def test_indentation_survives(self, args):
        """click's write_text would rewrap this into the preceding line."""
        assert _INDENTED_LINE in _run(*args, "--help")


@pytest.mark.parametrize("args", GROUPS[1:], ids=lambda a: " ".join(a))
def test_group_without_subcommand_has_the_footer(args):
    """The listing an agent gets from a bare group carries the pointer too."""
    assert "Are you an AI?" in _run(*args)


@pytest.mark.parametrize("args", LEAVES, ids=lambda a: " ".join(a))
def test_leaf_help_has_no_footer(args):
    assert "Are you an AI?" not in _run(*args, "--help")


def test_footer_names_the_configuration_sources():
    for source in ("PHAB_URL", "PHAB_TOKEN", ".arcrc", ".arcconfig"):
        assert source in AGENT_HELP_FOOTER
