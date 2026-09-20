# -*- coding: utf-8 -*-
"""User commands for phabfive CLI."""

import sys

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.exceptions import PhabfiveConfigException

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
def setup(ctx: typer.Context) -> None:
    """Configure phabfive with your Phabricator URL and API token."""
    from phabfive.setup import SetupWizard

    wizard = SetupWizard()
    if wizard.run():
        raise typer.Exit(0)
    else:
        raise typer.Exit(1)
