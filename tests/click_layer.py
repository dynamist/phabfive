# -*- coding: utf-8 -*-
"""The click that typer builds phabfive's commands on.

Up to typer 0.25 that is the click package. From 0.26 typer carries its own
copy, `typer._click`, and its commands, contexts and completion registry
belong to that copy: `typer.main.get_command(app)` is no longer a
`click.Group` (typer's copy has no Group at all), and `click.shell_completion.get_completion_class` finds none
of the classes typer registers. A test that drives typer's commands through
click's API uses this module instead, so it runs against whichever click
typer is actually using.

`pyproject.toml` allows any typer from 0.12, so both layouts are supported.
"""

import typer.testing

try:
    from typer._click import shell_completion
except ImportError:
    from click import shell_completion

# The stream CliRunner swaps in for stdin, whose isatty a test patches to
# act interactive. typer 0.26 moved its runner, and this class, into
# typer.testing
try:
    NamedTextIOWrapper = typer.testing._NamedTextIOWrapper
except AttributeError:
    from click.testing import _NamedTextIOWrapper as NamedTextIOWrapper

get_completion_class = shell_completion.get_completion_class


class ShellComplete(shell_completion.ShellComplete):
    """click's completion base, made concrete.

    typer's copy declares the shell-facing methods abstract. A test that only
    asks `get_completions` what is offered has no shell to speak to, so
    these are never called.
    """

    def source_vars(self):
        return {}

    def get_completion_args(self):
        return [], ""

    def format_completion(self, item):
        return item.value


__all__ = [
    "NamedTextIOWrapper",
    "ShellComplete",
    "get_completion_class",
    "shell_completion",
]
