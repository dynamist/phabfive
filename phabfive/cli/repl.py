# -*- coding: utf-8 -*-
"""REPL command for phabfive CLI."""

import typer


repl_app = typer.Typer(
    help="Enter a REPL with API access",
    invoke_without_command=True,
)


@repl_app.callback(invoke_without_command=True)
def repl_callback(ctx: typer.Context) -> None:
    """Start an interactive REPL session with Phabricator API access."""
    from phabfive.cli.apps import get_app
    from phabfive.repl import Repl

    get_app(Repl).run()
