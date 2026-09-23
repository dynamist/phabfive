# -*- coding: utf-8 -*-
"""Passphrase commands for phabfive CLI."""

from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.output import (
    _exit_with_help,
    _get_output_format,
    _setup_output_options,
)
from phabfive.cli.completers import complete_passphrase_type
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)

passphrase_app = typer.Typer(
    cls=AgentFooterGroup, help="The passphrase app", no_args_is_help=True
)


def _get_passphrase_app():
    """Get Passphrase app instance with config error handling."""
    from phabfive.cli.apps import get_app
    from phabfive.passphrase import Passphrase

    return get_app(Passphrase)


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

    \b
    Examples:
        phabfive passphrase show K1
        phabfive passphrase show K1 K2 K3
        phabfive passphrase show K1,K2,K3
        phabfive K1  # shortcut
    """
    from phabfive.passphrase.display import display_passphrases

    _setup_output_options(ctx)
    passphrase = _get_passphrase_app()

    # Support both space-separated (K1 K2) and comma-separated (K1,K2,K3)
    all_ids: list[str] = []
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
            from phabfive.passphrase.display import display_passphrase

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
    text_query: Optional[str] = typer.Argument(
        None, help="Search by name (partial match)"
    ),
    with_template: Optional[str] = typer.Option(
        None,
        "--with",
        help="Load the search from a YAML search spec; every option below "
        "overrides what the spec says",
    ),
    credential_type: Optional[str] = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by type: password, token, key, note",
        autocompletion=complete_passphrase_type,
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
        help="Maximum results to return, 0 for all",
    ),
) -> None:
    """Search credentials by name or type.

    \b
    Examples:
        phabfive passphrase search "deploy"
        phabfive passphrase search --type=password
        phabfive passphrase search "api" --type=token
        phabfive passphrase search --type=key --limit=0
        phabfive passphrase search --with searches.yaml
        phabfive --format=json passphrase search --type=key

    Both filters are applied here rather than by Phorge: passphrase.query
    takes no constraints, so a search reads every credential the token can
    see and tests each one. A small --limit shortens that walk once enough
    have matched, it does not make the query cheap.
    """
    from phabfive.passphrase.display import display_passphrases_list

    # Require at least one search criterion - unless a spec carries them,
    # which is checked per search once the spec has been read
    if not with_template and not text_query and not credential_type:
        _exit_with_help(ctx)

    # A spec never fetches secret material: the filters are applied in
    # Python over every credential on the instance, so a search asking for
    # secrets would fetch every secret rather than the ones that matched
    if with_template and show_secret:
        typer.echo(
            "ERROR: --show-secret cannot be combined with --with; a search "
            "spec never fetches secret material, use `passphrase show` for "
            "one credential",
            err=True,
        )
        raise typer.Exit(1)

    _setup_output_options(ctx)
    passphrase = _get_passphrase_app()

    if with_template:
        from phabfive.cli.search_spec import load_search_spec, run_search_spec

        run_search_spec(
            ctx,
            passphrase,
            load_search_spec(with_template),
            # Keyed as a spec spells the key; None means "not given", so a
            # flag nobody typed cannot clobber the spec's value
            overrides={
                "text_query": text_query,
                "type": credential_type,
                "limit": limit if limit != 100 else None,
            },
        )
        return

    try:
        credentials = passphrase.search_passphrases(
            query=text_query,
            credential_type=credential_type,
            need_secrets=show_secret,
            # A limit counts matching credentials, and 0 - as in `paste
            # search` and `maniphest search` - means every match
            limit=limit if limit > 0 else None,
        )

        output_format = _get_output_format(ctx)

        if not credentials:
            if text_query or credential_type:
                typer.echo("No credentials found matching the criteria", err=True)
            else:
                typer.echo("No credentials found", err=True)
            raise typer.Exit(0)

        display_passphrases_list(
            credentials, output_format, passphrase, show_secrets=show_secret
        )

    except (
        PhabfiveConfigException,
        PhabfiveDataException,
        PhabfiveRemoteException,
    ) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
