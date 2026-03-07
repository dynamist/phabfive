# -*- coding: utf-8 -*-
"""User commands for phabfive CLI."""

import typer

from phabfive.exceptions import PhabfiveConfigException

user_app = typer.Typer(help="Information on users, setup wizard")


@user_app.command()
def whoami(ctx: typer.Context) -> None:
    """Display information about the currently authenticated user."""
    from phabfive.user import User

    try:
        user = User()
        whoami_data = user.whoami()
        for key, value in whoami_data.items():
            typer.echo(f"{key}: {value}")
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
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
