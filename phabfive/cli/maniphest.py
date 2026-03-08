# -*- coding: utf-8 -*-
"""Maniphest commands for phabfive CLI."""

import re
import sys
from typing import List, Optional

import typer

from phabfive.constants import MONOGRAMS
from phabfive.exceptions import PhabfiveConfigException

maniphest_app = typer.Typer(help="The maniphest app")


def _get_maniphest_app():
    """Get Maniphest app instance with config error handling."""
    from phabfive.maniphest import Maniphest

    try:
        return Maniphest()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Maniphest()


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
        display_task_json,
        display_task_rich,
        display_task_yaml,
        display_task_tree,
    )

    if not result or not result.get("tasks"):
        return

    console = maniphest_instance.get_console()

    try:
        for task_dict in result["tasks"]:
            if output_format == "tree":
                display_task_tree(console, task_dict, maniphest_instance)
            elif output_format in ("yaml", "strict"):
                display_task_yaml(task_dict)
            elif output_format == "json":
                display_task_json(task_dict)
            else:  # "rich" (default)
                display_task_rich(console, task_dict, maniphest_instance)
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(0)


@maniphest_app.command()
def show(
    ctx: typer.Context,
    ticket_id: str = typer.Argument(..., help="Task ID (e.g., T123)"),
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
    """Show details for a Maniphest task."""
    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # Validate ticket ID format
    maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
    if not re.match(maniphest_pattern, ticket_id):
        typer.echo(f"Invalid task ID '{ticket_id}'. Expected format: T123", err=True)
        raise typer.Exit(1)

    task_id = int(ticket_id[1:])
    result = maniphest.task_show(
        task_id,
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
    title: Optional[str] = typer.Argument(None, help="Task title (for CLI mode)"),
    with_template: Optional[str] = typer.Option(
        None, "--with", help="Load task creation template from YAML file"
    ),
    description: Optional[str] = typer.Option(
        None, "--description", help="Task description"
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", help="Project/workboard tag (repeatable)"
    ),
    assign: Optional[str] = typer.Option(None, "--assign", help="Assignee username"),
    status: Optional[str] = typer.Option(None, "--status", help="Task status"),
    priority: Optional[str] = typer.Option(None, "--priority", help="Task priority"),
    subscribe: Optional[List[str]] = typer.Option(
        None, "--subscribe", help="Subscriber username (repeatable)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview without creating task"
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
        # CLI mode
        result = maniphest.create_task(
            title=title,
            description=description,
            tags=tag,
            assignee=assign,
            status=status,
            priority=priority,
            subscribers=subscribe,
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
        None, "--tag", help="Filter by project/workboard tag (supports wildcards)"
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
        None, "--column", help="Filter tasks by column transitions"
    ),
    priority: Optional[str] = typer.Option(
        None, "--priority", help="Filter tasks by priority transitions"
    ),
    status: Optional[str] = typer.Option(
        None, "--status", help="Filter tasks by status transitions"
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
