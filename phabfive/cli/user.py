# -*- coding: utf-8 -*-
"""User commands for phabfive CLI."""

import sys
from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.apps import new_app
from phabfive.cli.completers import (
    complete_user_role,
    complete_user_role_or_any,
    complete_username_list,
)
from phabfive.cli.output import (
    _exit_with_help,
    _get_output_format,
    _setup_output_options,
)
from phabfive.constants import (
    USER_ORDER_DEFAULT,
    USER_ORDER_DIRECTIONS,
    USER_ORDER_FIELDS,
    USER_ROLE_ANY,
)
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveConnectionException,
    PhabfiveRemoteException,
)
from phabfive.options import any_list_value, split_list_option
from phabfive.ordering import complete_order_value

user_app = typer.Typer(
    cls=AgentFooterGroup,
    help="Information on users, setup wizard",
    no_args_is_help=True,
)


def complete_user_order(incomplete: str) -> List[str]:
    """Complete `user search --order`: fields first, directions after ":"."""
    return complete_order_value(incomplete, USER_ORDER_FIELDS, USER_ORDER_DIRECTIONS)


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
    from phabfive.display import display_users
    from phabfive.user import User

    _setup_output_options(ctx)

    try:
        user = new_app(User)

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
    except PhabfiveConnectionException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


@user_app.command()
def search(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(
        None, help="Free text to match against usernames and real names"
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        help="Only users with this text anywhere in their username",
    ),
    realname: Optional[str] = typer.Option(
        None,
        "--realname",
        help="Only users with this text anywhere in their real name",
    ),
    role: Optional[List[str]] = typer.Option(
        None,
        "--role",
        help=(
            "Only users with every one of these roles (repeatable, or "
            "comma-separated); any lists every user"
        ),
        autocompletion=complete_user_role_or_any,
    ),
    not_role: Optional[List[str]] = typer.Option(
        None,
        "--not-role",
        help="Only users with none of these roles (repeatable, or comma-separated)",
        autocompletion=complete_user_role,
    ),
    ids: Optional[List[str]] = typer.Option(
        None,
        "--ids",
        help="Only these user IDs, as numbers, with every other filter still "
        "applied (repeatable, or comma-separated)",
    ),
    phids: Optional[List[str]] = typer.Option(
        None,
        "--phids",
        help="Only these user PHIDs, with every other filter still applied "
        "(repeatable, or comma-separated)",
    ),
    usernames: Optional[List[str]] = typer.Option(
        None,
        "--usernames",
        help="Exactly these usernames, where --username matches any part of "
        "one (repeatable, or comma-separated)",
        # Not complete_user_list_filter: the constraint matches the stored
        # name exactly, so @me is refused here and must not be offered.
        autocompletion=complete_username_list,
    ),
    created_after: Optional[str] = typer.Option(
        None,
        "--created-after",
        help="Users created within TIME, e.g. 1h, 7d, 2w",
    ),
    created_before: Optional[str] = typer.Option(
        None,
        "--created-before",
        help="Users created more than TIME ago, e.g. 1h, 7d, 2w",
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", "-M", help="Display metadata about each user"
    ),
    limit: int = typer.Option(
        100, "--limit", "-l", help="Maximum results to return, 0 for all"
    ),
    order: Optional[str] = typer.Option(
        None,
        "--order",
        "-o",
        help="Sort results by " + "|".join(USER_ORDER_FIELDS) + ", optionally "
        f"suffixed with :asc or :desc  [default: {USER_ORDER_DEFAULT}]",
        autocompletion=complete_user_order,
    ),
) -> None:
    """Search users, and filter them by role.

    Each user is listed with their roles as Phorge reports them: disabled,
    bot, list (a mailing list), admin, verified, approved and activated. The
    record is the one `project show --show-members` gives for each member,
    so the two can be compared directly.

    QUERY is found in the username or the real name, --username only in the
    username and --realname only in the real name. Each matches any part of
    the field, ignoring case and accents, and they combine: --realname=holm
    --username=r finds the Holms whose username has an r in it.

    --role keeps users with every role named, --not-role drops users with
    any of them. So every active person, with bots, mailing lists and
    disabled accounts left out, is --not-role=bot,list,disabled.

    A bare search prints this help rather than reading every user on the
    instance. --role=any is how to ask for all of them on purpose.

    Where a filter is applied decides what a search costs. --ids, --phids,
    --usernames, --created-after, --created-before and the disabled, bot,
    list and admin roles are constraints user.search applies itself, so
    only matching users cross the wire. QUERY, --username, --realname and
    the verified, approved and activated roles have no constraint of their
    own and are matched on the records here, so a search using only those
    reads every user the token can see, a page at a time, stopping once
    --limit of them have matched.

    \b
    Examples:
        phabfive user search viola
        phabfive user search --username=holm
        phabfive user search --realname="Larsson"
        phabfive user search --usernames=admin,deploy.bot
        phabfive user search --role=admin
        phabfive user search --created-after=30d --order=created
        phabfive user search --role=any -l 0
        phabfive --format=jsonl user search --not-role=bot,list,disabled -l 0
    """
    from phabfive.record_display import display_records
    from phabfive.user import User

    roles = split_list_option(role)
    not_roles = split_list_option(not_role)

    # A bare search prints help rather than reading every user on the
    # instance, as `maniphest search` does. --role=any is the explicit way to
    # ask for everyone; it requires no role, so it is dropped before the
    # search and only counts as having asked for something.
    # --order is not a criterion: it says how to sort a search, not which
    # users to look at.
    # The list options go through `any_list_value` rather than being tested
    # raw: `--usernames=,` is truthy as typer collected it and empty once it
    # is parsed, so the guard would lift and the search would then send no
    # constraint at all and read every user on the instance.
    if not any(
        [
            query,
            username,
            realname,
            roles,
            not_roles,
            created_after,
            created_before,
            any_list_value(ids, phids, usernames),
        ]
    ):
        _exit_with_help(ctx)
    roles = [r for r in roles if r != USER_ROLE_ANY]

    _setup_output_options(ctx)

    try:
        user = new_app(User)
        records = user.search(
            query=query,
            username=username,
            realname=realname,
            roles=roles,
            not_roles=not_roles,
            ids=split_list_option(ids),
            phids=split_list_option(phids),
            usernames=split_list_option(usernames),
            created_after=created_after,
            created_before=created_before,
            order=order,
            show_metadata=show_metadata,
            # A limit is how many users to return, not the page size to ask
            # for, and 0 - like every other search - means every match
            limit=limit if limit > 0 else None,
        )
    # PhabfiveInputException is a PhabfiveConfigException, so it is caught
    # here without being named: see phabfive/exceptions.py.
    except PhabfiveConfigException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except PhabfiveConnectionException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)
    except PhabfiveRemoteException as e:
        typer.echo(f"ERROR: {e}", err=True)
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
