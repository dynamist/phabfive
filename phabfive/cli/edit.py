# -*- coding: utf-8 -*-
"""Edit commands for phabfive CLI."""

from typing import Optional

import typer

from phabfive.cli.completers import complete_priority, complete_status
from phabfive.exceptions import PhabfiveConfigException


def _get_edit_app():
    """Get Edit app instance with config error handling."""
    from phabfive.edit import Edit

    try:
        return Edit()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Edit()


def edit_command(
    object_id: Optional[str] = typer.Argument(
        None,
        help="Object monogram(s) to edit (e.g., T123 or T123,T124,T125). Routes to app-specific edit command. If omitted, reads YAML from stdin.",
    ),
    priority: Optional[str] = typer.Option(
        None,
        "--priority",
        help="Set priority: unbreak, high, normal, low, wish, raise, lower",
        autocompletion=complete_priority,
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Set status: open, resolved, wontfix, invalid, duplicate, etc.",
        autocompletion=complete_status,
    ),
    tag: Optional[str] = typer.Option(
        None,
        "--tag",
        help="Specify board context for --column (also adds task to board if needed)",
    ),
    column: Optional[str] = typer.Option(
        None,
        "--column",
        help="Set column on board, or use forward/backward for directional navigation",
    ),
    assign: Optional[str] = typer.Option(
        None,
        "--assign",
        help="Set assignee (username or @me for yourself)",
    ),
    comment: Optional[str] = typer.Option(
        None,
        "--comment",
        help="Add comment with changes",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show changes without applying them",
    ),
) -> None:
    """Edit monograms (routes to app-specific edit command)

    Expands to app-specific edit commands based on monogram prefix:
    - phabfive edit T123 → phabfive maniphest edit T123
    - phabfive edit P456 → phabfive paste edit P456 (planned)
    - phabfive edit K789 → phabfive passphrase edit K789 (planned)

    For piped input, this command reads YAML from stdin.

    Examples:
        phabfive edit T123 --priority=raise --status=resolved
        phabfive maniphest search --tag "Backend" | phabfive edit --column=Done
        phabfive edit T123 --tag="Sprint" --column=forward --comment="Moving forward"
    """
    edit_handler = _get_edit_app()

    retcode = edit_handler.edit_objects(
        object_id=object_id,
        priority=priority,
        status=status,
        tag=tag,
        column=column,
        assign=assign,
        comment=comment,
        dry_run=dry_run,
    )

    raise typer.Exit(retcode)
