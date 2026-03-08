# -*- coding: utf-8 -*-
"""Passphrase commands for phabfive CLI."""

import typer

from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException

passphrase_app = typer.Typer(help="The passphrase app")


def _get_passphrase_app():
    """Get Passphrase app instance with config error handling."""
    from phabfive.passphrase import Passphrase

    try:
        return Passphrase()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Passphrase()


@passphrase_app.command()
def show(
    ctx: typer.Context,
    id: str = typer.Argument(..., help="Passphrase ID (e.g., K123)"),
) -> None:
    """Retrieve a secret from Passphrase by ID."""
    passphrase = _get_passphrase_app()
    try:
        secret = passphrase.get_secret(id)
        typer.echo(secret)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
