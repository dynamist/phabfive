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
    complete_tag_list,
    complete_user,
    complete_user_list,
)
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.cli.editor import resolve_assume_yes
from phabfive.commits import COMMIT_GRAMMAR
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
    tag: Optional[List[str]] = typer.Option(
        None,
        "--tag",
        help="Add a project tag (name, #hashtag, ID or PHID; repeatable, or comma-separated); the first is the board for --column",
        autocompletion=complete_tag_list,
    ),
    add_tag: Optional[List[str]] = typer.Option(
        None,
        "--add-tag",
        hidden=True,
        help="Alias for --tag",
        autocompletion=complete_tag_list,
    ),
    untag: Optional[List[str]] = typer.Option(
        None,
        "--untag",
        help="Remove a project tag (name, #hashtag, ID or PHID; repeatable, or comma-separated)",
        autocompletion=complete_tag_list,
    ),
    remove_tag: Optional[List[str]] = typer.Option(
        None,
        "--remove-tag",
        hidden=True,
        help="Alias for --untag",
        autocompletion=complete_tag_list,
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
    unassign: bool = typer.Option(
        False,
        "--unassign",
        help="Remove the assignee",
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Set description (use - to read from stdin, or omit all options to open $EDITOR)",
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None,
        "--subscribe",
        help="Add a subscriber (username, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list,
    ),
    add_subscriber: Optional[List[str]] = typer.Option(
        None,
        "--add-subscriber",
        hidden=True,
        help="Alias for --subscribe",
        autocompletion=complete_user_list,
    ),
    unsubscribe: Optional[List[str]] = typer.Option(
        None,
        "--unsubscribe",
        help="Remove a subscriber (username, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list,
    ),
    remove_subscriber: Optional[List[str]] = typer.Option(
        None,
        "--remove-subscriber",
        hidden=True,
        help="Alias for --unsubscribe",
        autocompletion=complete_user_list,
    ),
    attach: Optional[List[str]] = typer.Option(
        None,
        "--attach",
        help=f"Attach a commit ({COMMIT_GRAMMAR}; repeatable, or comma-separated)",
    ),
    add_commit: Optional[List[str]] = typer.Option(
        None,
        "--add-commit",
        hidden=True,
        help="Alias for --attach",
    ),
    detach: Optional[List[str]] = typer.Option(
        None,
        "--detach",
        help=f"Detach a commit ({COMMIT_GRAMMAR}; repeatable, or comma-separated)",
    ),
    remove_commit: Optional[List[str]] = typer.Option(
        None,
        "--remove-commit",
        hidden=True,
        help="Alias for --detach",
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

    \b
    Examples:
        phabfive edit T123 --priority=raise --status=resolved
        phabfive maniphest search --tag "Backend" | phabfive edit --column=Done
        phabfive edit T123 --tag="Sprint" --column=forward --comment="Moving forward"
        phabfive edit T123 --tag=Backend,QA --untag=Triage
        phabfive edit T123 T124 --untag=Sprint
        phabfive edit T123 T124 --space=Archive
        phabfive edit T123 --unassign
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
        tag=[*(tag or []), *(add_tag or [])],
        untag=[*(untag or []), *(remove_tag or [])],
        column=column,
        assign=assign,
        unassign=unassign,
        description=description,
        subscribe=[*(subscribe or []), *(add_subscriber or [])],
        unsubscribe=[*(unsubscribe or []), *(remove_subscriber or [])],
        attach=[*(attach or []), *(add_commit or [])],
        detach=[*(detach or []), *(remove_commit or [])],
        comment=comment,
        space=space,
        visible_to=visible_to,
        editable_by=editable_by,
        dry_run=dry_run,
        force=force,
        interactive=interactive,
    )

    raise typer.Exit(retcode)
