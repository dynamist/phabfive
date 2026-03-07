# -*- coding: utf-8 -*-
"""Passphrase commands for phabfive CLI."""

import typer

from phabfive.exceptions import PhabfiveConfigException

passphrase_app = typer.Typer(
    help="The passphrase app",
    invoke_without_command=True,
)


@passphrase_app.callback(invoke_without_command=True)
def passphrase_callback(
    ctx: typer.Context,
    id: str = typer.Argument(..., help="Passphrase ID (e.g., K123)"),
) -> None:
    """Retrieve a secret from Passphrase by ID."""
    from phabfive.passphrase import Passphrase

    try:
        passphrase = Passphrase()
        secret = passphrase.get_secret(id)
        typer.echo(secret)
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
