# -*- coding: utf-8 -*-
"""Maniphest commands for phabfive CLI."""

import re
import sys
from typing import List, Optional

import typer

from phabfive.cli.completers import (
    complete_column,
    complete_priority,
    complete_status,
    complete_tag,
)
from phabfive.constants import MONOGRAMS
from phabfive.exceptions import PhabfiveConfigException

maniphest_app = typer.Typer(help="The maniphest app")


def _get_maniphest_app():
    """Get Maniphest app instance with config error handling."""
    import requests

    from phabfive.maniphest import Maniphest

    try:
        return Maniphest()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Maniphest()
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


def _get_output_format(ctx: typer.Context):
    """Get output format from context or auto-detect."""
    from phabfive.core import Phabfive

    format_arg = ctx.obj.get("format") if ctx.obj else None
    if format_arg is None:
        return Phabfive._get_auto_format()
    return format_arg


def _setup_output_options(ctx: typer.Context):
    """Set up output options from context."""
    from phabfive.core import Phabfive

    if ctx.obj:
        ascii_when = ctx.obj.get("ascii", "auto")
        hyperlink_when = ctx.obj.get("hyperlink", "auto")
        output_format = _get_output_format(ctx)

        Phabfive.set_output_options(
            ascii_when=ascii_when,
            hyperlink_when=hyperlink_when,
            output_format=output_format,
        )


def _display_tasks(result, output_format, maniphest_instance):
    """Display task search/show results in the specified format."""
    from phabfive.display import (
        display_tasks_json,
        display_tasks_rich,
        display_tasks_tree,
        display_tasks_yaml,
    )

    if not result or not result.get("tasks"):
        return

    console = maniphest_instance.get_console()

    try:
        tasks = result["tasks"]
        if output_format == "json":
            display_tasks_json(tasks)
        elif output_format == "tree":
            display_tasks_tree(console, tasks, maniphest_instance)
        elif output_format in ("yaml", "strict"):
            display_tasks_yaml(tasks)
        else:  # "rich" (default)
            display_tasks_rich(console, tasks, maniphest_instance)
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(0)


@maniphest_app.command()
def show(
    ctx: typer.Context,
    ticket_ids: List[str] = typer.Argument(..., help="Task ID(s) (e.g., T123 T456)"),
    show_history: bool = typer.Option(
        False, "--show-history", "-H", help="Display transition history"
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", "-M", help="Display metadata about the task"
    ),
    show_comments: bool = typer.Option(
        False, "--show-comments", "-C", help="Display comments on the task"
    ),
) -> None:
    """Show details for one or more Maniphest tasks."""
    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # Validate all ticket ID formats
    maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
    task_ids = []
    for ticket_id in ticket_ids:
        if not re.match(maniphest_pattern, ticket_id):
            typer.echo(
                f"Invalid task ID '{ticket_id}'. Expected format: T123", err=True
            )
            raise typer.Exit(1)
        task_ids.append(int(ticket_id[1:]))

    result = maniphest.task_show(
        task_ids,
        show_history=show_history,
        show_metadata=show_metadata,
        show_comments=show_comments,
    )

    output_format = _get_output_format(ctx)
    _display_tasks(result, output_format, maniphest)


@maniphest_app.command()
def comment(
    ctx: typer.Context,
    ticket_id: str = typer.Argument(..., help="Task ID (e.g., T123)"),
    comment_text: str = typer.Argument(..., help="Comment text to add"),
) -> None:
    """Add a comment to a Maniphest task."""
    maniphest = _get_maniphest_app()

    result = maniphest.add_task_comment(ticket_id, comment_text)

    if result[0]:
        # Query the ticket to fetch the URI for it
        _, ticket = maniphest.get_task_info(int(ticket_id[1:]))
        typer.echo(ticket["uri"])


@maniphest_app.command()
def create(
    ctx: typer.Context,
    title: Optional[str] = typer.Argument(
        None, help="Task title (required unless using --with)"
    ),
    with_template: Optional[str] = typer.Option(
        None, "--with", help="Load task creation template from YAML file"
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Task description (use - to read from stdin, or omit to open $EDITOR)",
    ),
    tag: Optional[List[str]] = typer.Option(
        None,
        "--tag",
        help="Add to project/workboard (repeatable)",
        autocompletion=complete_tag,
    ),
    column: Optional[str] = typer.Option(
        None,
        "--column",
        help="Initial column on board (requires --tag)",
        autocompletion=complete_column,
    ),
    assign: Optional[str] = typer.Option(
        None,
        "--assign",
        help="Set assignee (username or @me for yourself)",
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Set status (open, resolved, wontfix, invalid, duplicate, etc.)",
        autocompletion=complete_status,
    ),
    priority: Optional[str] = typer.Option(
        None,
        "--priority",
        help="Set priority (unbreak, high, normal, low, wish)",
        autocompletion=complete_priority,
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None,
        "--subscribe",
        help="Add subscriber (username or @me, repeatable)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview without creating task"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip confirmation prompt (required for non-interactive use)",
    ),
) -> None:
    """Create a new Maniphest task."""
    maniphest = _get_maniphest_app()

    if with_template:
        # Template mode
        result = maniphest.create_tasks_from_yaml(with_template, dry_run=dry_run)
        if result and result.get("dry_run"):
            for task in result["tasks"]:
                indent = "  " * task["depth"]
                typer.echo(f"{indent}- {task['title']}")
    elif title:
        # CLI mode - handle description input modes
        final_description = description

        if description == "-":
            # Read from stdin
            if sys.stdin.isatty():
                sys.stderr.write("Error: --description - requires input from stdin\n")
                raise typer.Exit(1)
            final_description = sys.stdin.read().rstrip()
        elif description is None and sys.stdin.isatty() and not dry_run:
            # Open $EDITOR for description (only in interactive mode)
            from phabfive.editor import edit_text

            final_description = edit_text("")
            if final_description and not force:
                print()
                print(final_description)
                print()
                if not typer.confirm("Create task with this description?"):
                    print("Cancelled")
                    raise typer.Exit(0)

        # Validate --column requires --tag
        if column and not tag:
            sys.stderr.write("Error: --column requires --tag to specify board context\n")
            raise typer.Exit(1)

        # Resolve board PHID if column is specified
        board_phid = None
        if column and tag:
            # Use the first tag as the board context
            board_phids = maniphest._resolve_project_phids(tag[0])
            if board_phids:
                board_phid = board_phids[0]
            else:
                sys.stderr.write(f"Error: Board not found: {tag[0]}\n")
                raise typer.Exit(1)

        result = maniphest.create_task(
            title=title,
            description=final_description,
            tags=tag,
            assignee=assign,
            status=status,
            priority=priority,
            subscribers=subscribe,
            column=column,
            board_phid=board_phid,
            dry_run=dry_run,
        )
        if result:
            if result.get("dry_run"):
                typer.echo("\n--- DRY RUN ---")
                typer.echo(f"Title: {result['title']}")
                if result.get("description"):
                    typer.echo(f"Description: {result['description']}")
                if result.get("priority"):
                    typer.echo(f"Priority: {result['priority']}")
                if result.get("status"):
                    typer.echo(f"Status: {result['status']}")
                if result.get("assignee"):
                    typer.echo(f"Assignee: {result['assignee']}")
                if result.get("tags"):
                    typer.echo(f"Tags: {', '.join(result['tags'])}")
                if result.get("column"):
                    typer.echo(f"Column: {result['column']}")
                if result.get("subscribers"):
                    typer.echo(f"Subscribers: {', '.join(result['subscribers'])}")
                typer.echo("--- END DRY RUN ---\n")
            else:
                typer.echo(result["uri"])
                if result.get("tag_slugs"):
                    for slug in result["tag_slugs"]:
                        typer.echo(f"{result['base_url']}/tag/{slug}/")
    else:
        typer.echo("ERROR: Must provide either a title or --with=TEMPLATE", err=True)
        raise typer.Exit(1)


@maniphest_app.command()
def search(
    ctx: typer.Context,
    text_query: Optional[str] = typer.Argument(
        None, help="Free-text search in task title/description"
    ),
    with_template: Optional[str] = typer.Option(
        None, "--with", help="Load search parameters from a YAML template file"
    ),
    tag: Optional[str] = typer.Option(
        None,
        "--tag",
        help="Filter by project/workboard tag (supports wildcards)",
        autocompletion=complete_tag,
    ),
    assigned: Optional[str] = typer.Option(
        None, "--assigned", help="Filter by assignee. Use @me for yourself."
    ),
    space: Optional[str] = typer.Option(
        None, "--space", help="Filter by Space (supports wildcards)"
    ),
    created_after: Optional[str] = typer.Option(
        None, "--created-after", help="Tasks created within TIME (e.g., 1h, 7d, 2w)"
    ),
    created_before: Optional[str] = typer.Option(
        None, "--created-before", help="Tasks created more than TIME ago"
    ),
    updated_after: Optional[str] = typer.Option(
        None, "--updated-after", help="Tasks updated within TIME (e.g., 1h, 7d, 2w)"
    ),
    updated_before: Optional[str] = typer.Option(
        None, "--updated-before", help="Tasks updated more than TIME ago"
    ),
    include_all: bool = typer.Option(False, "--all", help="Include closed tasks"),
    column: Optional[str] = typer.Option(
        None,
        "--column",
        help="Filter tasks by column transitions",
        autocompletion=complete_column,
    ),
    priority: Optional[str] = typer.Option(
        None,
        "--priority",
        help="Filter tasks by priority transitions",
        autocompletion=complete_priority,
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter tasks by status transitions",
        autocompletion=complete_status,
    ),
    show_history: bool = typer.Option(
        False, "--show-history", help="Display transition history"
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", help="Display filter match metadata"
    ),
) -> None:
    """Search for Maniphest tasks."""
    from phabfive.transitions import parse_column_patterns, parse_priority_patterns

    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # Load YAML configurations if --with is provided
    search_configs = []
    if with_template:
        try:
            search_configs = maniphest._load_search_config(with_template)
        except Exception as e:
            typer.echo(f"ERROR: Failed to load template file: {e}", err=True)
            raise typer.Exit(1)
    else:
        search_configs = [
            {
                "search": {},
                "title": "Command Line Search",
                "description": None,
            }
        ]

    def get_param(cli_value, yaml_params, yaml_key, default=None):
        """Get value with CLI override priority."""
        if cli_value is not None:
            return cli_value
        return yaml_params.get(yaml_key, default)

    # Execute each search configuration
    for config in search_configs:
        yaml_params = config["search"]

        # Print search header if multiple searches or if title/description provided
        if len(search_configs) > 1 or config["title"] != "Command Line Search":
            typer.echo(f"\n{'=' * 60}")
            typer.echo(f"🔍 {config['title']}")
            if config["description"]:
                typer.echo(f"📝 {config['description']}")
            typer.echo(f"{'=' * 60}")

        # Parse filter patterns with CLI override priority
        column_patterns = None
        column_pattern = get_param(column, yaml_params, "column")
        if column_pattern:
            try:
                column_patterns = parse_column_patterns(column_pattern)
            except Exception as e:
                typer.echo(f"ERROR: Invalid column filter pattern: {e}", err=True)
                raise typer.Exit(1)

        priority_patterns = None
        priority_pattern = get_param(priority, yaml_params, "priority")
        if priority_pattern:
            try:
                priority_patterns = parse_priority_patterns(priority_pattern)
            except Exception as e:
                typer.echo(f"ERROR: Invalid priority filter pattern: {e}", err=True)
                raise typer.Exit(1)

        status_patterns = None
        status_pattern = get_param(status, yaml_params, "status")
        if status_pattern:
            try:
                status_patterns = maniphest.parse_status_patterns_with_api(
                    status_pattern
                )
            except Exception as e:
                typer.echo(f"ERROR: Invalid status filter pattern: {e}", err=True)
                raise typer.Exit(1)

        # Get other parameters with CLI override priority
        final_show_history = get_param(
            show_history if show_history else None,
            yaml_params,
            "show-history",
            False,
        )
        final_show_metadata = get_param(
            show_metadata if show_metadata else None,
            yaml_params,
            "show-metadata",
            False,
        )
        final_text_query = get_param(text_query, yaml_params, "text_query")
        final_tag = get_param(tag, yaml_params, "tag")
        final_assigned = get_param(assigned, yaml_params, "assigned")
        final_space = get_param(space, yaml_params, "space")
        final_created_after = get_param(created_after, yaml_params, "created-after")
        final_created_before = get_param(created_before, yaml_params, "created-before")
        final_updated_after = get_param(updated_after, yaml_params, "updated-after")
        final_updated_before = get_param(updated_before, yaml_params, "updated-before")
        final_include_closed = get_param(
            include_all if include_all else None,
            yaml_params,
            "all",
            False,
        )

        # Check if any search criteria provided
        has_criteria = any(
            [
                final_text_query,
                final_tag,
                final_assigned,
                final_space,
                final_created_after,
                final_updated_after,
                column_patterns,
                priority_patterns,
                status_patterns,
            ]
        )
        if not has_criteria:
            typer.echo("Usage:")
            typer.echo("    phabfive maniphest search [<text_query>] [options]")
            return

        result = maniphest.task_search(
            text_query=final_text_query,
            tag=final_tag,
            assigned=final_assigned,
            space=final_space,
            created_after=final_created_after,
            created_before=final_created_before,
            updated_after=final_updated_after,
            updated_before=final_updated_before,
            column_patterns=column_patterns,
            priority_patterns=priority_patterns,
            status_patterns=status_patterns,
            show_history=final_show_history,
            show_metadata=final_show_metadata,
            include_closed=final_include_closed,
        )

        output_format = _get_output_format(ctx)
        _display_tasks(result, output_format, maniphest)


def _get_edit_app():
    """Get Edit app instance with config error handling."""
    import requests

    from phabfive.edit import Edit

    try:
        return Edit()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Edit()
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


@maniphest_app.command()
def edit(
    ctx: typer.Context,
    task_ids: str = typer.Argument(
        ...,
        help="Task monogram(s) (e.g., T123 or T123,T124,T125)",
    ),
    priority: Optional[str] = typer.Option(
        None,
        "--priority",
        help="Set priority (unbreak, high, normal, low, wish) or use raise/lower to navigate",
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
        autocompletion=complete_tag,
    ),
    column: Optional[str] = typer.Option(
        None,
        "--column",
        help="Set column by name, or use forward/backward to navigate",
        autocompletion=complete_column,
    ),
    assign: Optional[str] = typer.Option(
        None,
        "--assign",
        help="Set assignee (username or @me for yourself)",
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Set description (use - to read from stdin, or omit all options to open $EDITOR)",
    ),
    subscribe: Optional[List[str]] = typer.Option(
        None,
        "--subscribe",
        help="Add subscriber (username or @me, repeatable)",
    ),
    comment_text: Optional[str] = typer.Option(
        None,
        "--comment",
        help="Add comment with changes",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show changes without applying them",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip confirmation prompt (required for non-interactive use)",
    ),
) -> None:
    """Edit one or more Maniphest tasks.

    \b
    Examples:
        phabfive maniphest edit T123  # opens $EDITOR for description
        phabfive maniphest edit T123 --priority=high
        phabfive maniphest edit T123,T124 --status=resolved
        phabfive maniphest edit T123 --tag="Sprint" --column=forward
    """
    # Validate monogram format (T followed by digits)
    maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
    for part in task_ids.split(","):
        part = part.strip()
        if not re.match(maniphest_pattern, part):
            typer.echo(
                f"Invalid task monogram '{part}'. Expected format: T123", err=True
            )
            raise typer.Exit(1)

    # Delegate to Edit class for processing
    edit_handler = _get_edit_app()

    retcode = edit_handler.edit_objects(
        object_id=task_ids,
        priority=priority,
        status=status,
        tag=tag,
        column=column,
        assign=assign,
        description=description,
        subscribe=subscribe,
        comment=comment_text,
        dry_run=dry_run,
        force=force,
    )

    raise typer.Exit(retcode)


@maniphest_app.command()
def parents(
    ctx: typer.Context,
    ticket_id: str = typer.Argument(..., help="Task ID (e.g., T123)"),
) -> None:
    """List parent tasks of a Maniphest task."""
    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # Validate ticket ID format
    maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
    if not re.match(maniphest_pattern, ticket_id):
        typer.echo(f"Invalid task ID '{ticket_id}'. Expected format: T123", err=True)
        raise typer.Exit(1)

    task_id = int(ticket_id[1:])
    result = maniphest.get_related_tasks(task_id, "parents")

    if result is None:
        typer.echo(f"Task {ticket_id} not found", err=True)
        raise typer.Exit(1)

    if not result.get("tasks"):
        typer.echo(f"No parent tasks found for {ticket_id}")
        return

    output_format = _get_output_format(ctx)
    _display_tasks(result, output_format, maniphest)


@maniphest_app.command()
def subtasks(
    ctx: typer.Context,
    ticket_id: str = typer.Argument(..., help="Task ID (e.g., T123)"),
) -> None:
    """List subtasks of a Maniphest task."""
    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # Validate ticket ID format
    maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
    if not re.match(maniphest_pattern, ticket_id):
        typer.echo(f"Invalid task ID '{ticket_id}'. Expected format: T123", err=True)
        raise typer.Exit(1)

    task_id = int(ticket_id[1:])
    result = maniphest.get_related_tasks(task_id, "subtasks")

    if result is None:
        typer.echo(f"Task {ticket_id} not found", err=True)
        raise typer.Exit(1)

    if not result.get("tasks"):
        typer.echo(f"No subtasks found for {ticket_id}")
        return

    output_format = _get_output_format(ctx)
    _display_tasks(result, output_format, maniphest)
