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
    from phabfive.core import Phabfive
    from phabfive.passphrase_display import display_passphrase

    passphrase = _get_passphrase_app()
    try:
        data = passphrase.get_passphrase(id)

        format_arg = ctx.obj.get("format") if ctx.obj else None
        output_format = format_arg if format_arg else Phabfive._get_auto_format()
        display_passphrase(data, output_format, passphrase)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
