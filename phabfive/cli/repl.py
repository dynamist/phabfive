# -*- coding: utf-8 -*-
"""REPL command for phabfive CLI."""

import typer

from phabfive.exceptions import PhabfiveConfigException

repl_app = typer.Typer(
    help="Enter a REPL with API access",
    invoke_without_command=True,
)


@repl_app.callback(invoke_without_command=True)
def repl_callback(ctx: typer.Context) -> None:
    """Start an interactive REPL session with Phabricator API access."""
    from phabfive.repl import Repl

    try:
        repl = Repl()
        repl.run()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
