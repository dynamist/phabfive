# -*- coding: utf-8 -*-
"""Passphrase commands for phabfive CLI."""

import sys
from typing import List, Optional

import typer

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)

passphrase_app = typer.Typer(help="The passphrase app")


def _get_passphrase_app():
    """Get Passphrase app instance with config error handling."""
    import requests

    from phabfive.passphrase import Passphrase

    try:
        return Passphrase()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Passphrase()
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


def _get_output_format(ctx: typer.Context):
    """Get output format from context."""
    from phabfive.core import Phabfive

    format_arg = ctx.obj.get("format") if ctx.obj else None
    return format_arg if format_arg else Phabfive._get_auto_format()


@passphrase_app.command()
def show(
    ctx: typer.Context,
    ids: List[str] = typer.Argument(
        ..., help="Passphrase ID(s) (e.g., K1 K2 or K1,K2,K3)"
    ),
    no_secret: bool = typer.Option(
        False, "--no-secret", "-n", help="Hide the secret value"
    ),
    no_public_key: bool = typer.Option(
        False, "--no-public-key", "-P", help="Hide public key for SSH credentials"
    ),
) -> None:
    """Retrieve secrets from Passphrase by ID.

    Examples:
        phabfive passphrase show K1
        phabfive passphrase show K1 K2 K3
        phabfive passphrase show K1,K2,K3
        phabfive K1  # shortcut
        phabfive K1,K2  # shortcut for multiple
    """
    from phabfive.passphrase_display import display_passphrases

    passphrase = _get_passphrase_app()

    # Support both space-separated (K1 K2) and comma-separated (K1,K2,K3)
    all_ids = []
    for id_arg in ids:
        all_ids.extend(part.strip() for part in id_arg.split(",") if part.strip())

    try:
        output_format = _get_output_format(ctx)
        need_secrets = not no_secret
        need_public_keys = not no_public_key

        # Always use get_passphrases for consistent behavior
        data = passphrase.get_passphrases(
            all_ids,
            need_secrets=need_secrets,
            need_public_keys=need_public_keys,
        )

        if len(all_ids) == 1:
            # Single credential - use singular display
            from phabfive.passphrase_display import display_passphrase

            display_passphrase(data[0], output_format, passphrase)
        else:
            # Multiple credentials
            display_passphrases(
                data, output_format, passphrase, show_secrets=need_secrets
            )

    except (PhabfiveDataException, PhabfiveRemoteException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


@passphrase_app.command()
def search(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(None, help="Search by name (partial match)"),
    credential_type: Optional[str] = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by type: password, token, key, note",
    ),
    show_secret: bool = typer.Option(
        False,
        "--show-secret",
        "-s",
        help="Include secrets in output (hidden by default)",
    ),
    limit: int = typer.Option(
        100,
        "--limit",
        "-l",
        help="Maximum results to return",
    ),
) -> None:
    """List and search credentials.

    Examples:
        phabfive passphrase search
        phabfive passphrase search "deploy"
        phabfive passphrase search --type=password
        phabfive passphrase search --show-secret
        phabfive --format=json passphrase search
    """
    from phabfive.passphrase_display import display_passphrases_list

    passphrase = _get_passphrase_app()

    try:
        credentials = passphrase.search_passphrases(
            query=query,
            credential_type=credential_type,
            need_secrets=show_secret,
            limit=limit,
        )

        output_format = _get_output_format(ctx)

        if not credentials:
            if query or credential_type:
                typer.echo("No credentials found matching the criteria", err=True)
            else:
                typer.echo("No credentials found", err=True)
            raise typer.Exit(0)

        display_passphrases_list(
            credentials, output_format, passphrase, show_secrets=show_secret
        )

    except (PhabfiveDataException, PhabfiveRemoteException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
