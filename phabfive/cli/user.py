# -*- coding: utf-8 -*-
"""User commands for phabfive CLI."""

import sys
from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import complete_user_role
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.exceptions import PhabfiveConfigException, PhabfiveRemoteException
from phabfive.options import split_list_option

user_app = typer.Typer(
    cls=AgentFooterGroup,
    help="Information on users, setup wizard",
    no_args_is_help=True,
)


@user_app.command()
def whoami(
    ctx: typer.Context,
    all_hosts: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Query every host in ~/.arcrc, even when PHAB_URL selects one.",
    ),
) -> None:
    """Show the current user for the configured host.

    Reports the host phabfive is configured to use. When no host is
    configured outside ~/.arcrc, every host in ~/.arcrc is reported
    instead; --all forces that for a configured host too.
    """
    import requests

    from phabfive.display import display_users
    from phabfive.user import User

    _setup_output_options(ctx)

    try:
        user = User()

        if all_hosts or not user.has_explicit_phab_url():
            results = user.whoami_all_hosts()
        else:
            results = [user.whoami_configured_host()]

        if not results:
            typer.echo("No hosts found in ~/.arcrc", err=True)
            raise typer.Exit(1)

        output_format = _get_output_format(ctx)
        display_users(results, output_format, user)

        # Every host failed, so the command did not do what was asked
        if all(result.get("Error") for result in results):
            raise typer.Exit(1)
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


@user_app.command()
def search(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(
        None, help="Free text to match against usernames and real names"
    ),
    role: Optional[List[str]] = typer.Option(
        None,
        "--role",
        help="Only users with every one of these roles (repeatable, or comma-separated)",
        autocompletion=complete_user_role,
    ),
    not_role: Optional[List[str]] = typer.Option(
        None,
        "--not-role",
        help="Only users with none of these roles (repeatable, or comma-separated)",
        autocompletion=complete_user_role,
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", "-M", help="Display metadata about each user"
    ),
    limit: int = typer.Option(
        100, "--limit", "-l", help="Maximum results to return, 0 for all"
    ),
) -> None:
    """Search users, and filter them by role.

    Each user is listed with their roles as Phorge reports them: disabled,
    bot, list (a mailing list), admin, verified, approved and activated. The
    record is the one `project show --show-members` gives for each member,
    so the two can be compared directly.

    --role keeps users with every role named, --not-role drops users with
    any of them. So every active person, with bots, mailing lists and
    disabled accounts left out, is --not-role=bot,list,disabled.

    \b
    Examples:
        phabfive user search
        phabfive user search viola
        phabfive user search --role=admin
        phabfive --format=jsonl user search --not-role=bot,list,disabled -l 0
    """
    import requests

    from phabfive.record_display import display_records
    from phabfive.user import User

    _setup_output_options(ctx)

    try:
        user = User()
        records = user.search(
            query=query,
            roles=split_list_option(role),
            not_roles=split_list_option(not_role),
            show_metadata=show_metadata,
            # A limit is how many users to return, not the page size to ask
            # for, and 0 - like every other search - means every match
            limit=limit if limit > 0 else None,
        )
    except PhabfiveConfigException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except PhabfiveRemoteException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)

    if not records:
        typer.echo("No users found", err=True)
        return

    display_records(records, _get_output_format(ctx), user, tabular=True)


@user_app.command()
def setup(ctx: typer.Context) -> None:
    """Configure phabfive with your Phabricator URL and API token."""
    from phabfive.setup import SetupWizard

    wizard = SetupWizard()
    if wizard.run():
        raise typer.Exit(0)
    else:
        raise typer.Exit(1)
