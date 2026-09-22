# -*- coding: utf-8 -*-
"""Edit commands for phabfive CLI."""

import sys
from typing import List, Optional

import typer

from phabfive.cli.completers import (
    complete_column_change,
    complete_policy,
    complete_priority_change,
    complete_space,
    complete_status,
    complete_tag,
    complete_user,
    complete_user_list,
)
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.cli.editor import resolve_assume_yes
from phabfive.policy import POLICY_GRAMMAR


def _get_edit_app():
    """Get Edit app instance with config error handling."""
    from phabfive.cli.apps import get_app
    from phabfive.edit import Edit

    return get_app(Edit)


def edit_command(
    ctx: typer.Context,
    object_id: Optional[str] = typer.Argument(
        None,
        help="Object monogram(s) to edit (e.g., T123 or T123,T124,T125). Routes to app-specific edit command. If omitted, reads YAML from stdin.",
    ),
    priority: Optional[str] = typer.Option(
        None,
        "--priority",
        help="Set priority (unbreak, high, normal, low, wish) or use raise/lower to navigate",
        autocompletion=complete_priority_change,
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
        autocompletion=complete_tag,
    ),
    column: Optional[str] = typer.Option(
        None,
        "--column",
        help="Set column by name, or use forward/backward to navigate",
        autocompletion=complete_column_change,
    ),
    assign: Optional[str] = typer.Option(
        None,
        "--assign",
        help="Set assignee (username, @me for yourself, or a user PHID)",
        autocompletion=complete_user,
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Set description (use - to read from stdin, or omit all options to open $EDITOR)",
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None,
        "--subscribe",
        help="Add subscriber (username, @me or user PHID, repeatable, comma-separated)",
        autocompletion=complete_user_list,
    ),
    comment: Optional[str] = typer.Option(
        None,
        "--comment",
        help="Add comment with changes",
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Move to a Space (monogram, name, or unique pattern)",
        autocompletion=complete_space,
    ),
    visible_to: Optional[str] = typer.Option(
        None,
        "--visible-to",
        help=f"Set who can see it ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    ),
    editable_by: Optional[str] = typer.Option(
        None,
        "--editable-by",
        help=f"Set who can edit it ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show changes without applying them",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Apply without confirming",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Review each change and confirm",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        hidden=True,
        help="Deprecated alias for --yes",
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
        phabfive edit T123 T124 --space=Archive
        phabfive edit T123 --visible-to=public --editable-by='#infra'
    """
    try:
        force = resolve_assume_yes(yes, force, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    _setup_output_options(ctx)
    edit_handler = _get_edit_app()

    # Imported here: it pulls in the edit planning, which a command that is
    # only completing or printing help should not pay for
    from phabfive.cli import edit_flow

    retcode = edit_flow.run_edit(
        edit_handler,
        object_id=object_id,
        output_format=_get_output_format(ctx),
        priority=priority,
        status=status,
        tag=tag,
        column=column,
        assign=assign,
        description=description,
        subscribe=subscribe,
        comment=comment,
        space=space,
        visible_to=visible_to,
        editable_by=editable_by,
        dry_run=dry_run,
        force=force,
        interactive=interactive,
    )

    raise typer.Exit(retcode)
