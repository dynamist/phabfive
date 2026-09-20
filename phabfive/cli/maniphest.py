# -*- coding: utf-8 -*-
"""Maniphest commands for phabfive CLI."""

import re
import sys
from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import (
    complete_column,
    complete_column_change,
    complete_column_filter,
    complete_order,
    complete_priority,
    complete_priority_change,
    complete_priority_filter,
    complete_space,
    complete_space_filter,
    complete_status,
    complete_status_filter,
    complete_tag,
    complete_user,
    complete_user_list_filter,
)
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.constants import MONOGRAMS
from phabfive.editor import resolve_assume_yes
from phabfive.exceptions import PhabfiveConfigException

maniphest_app = typer.Typer(
    cls=AgentFooterGroup, help="The maniphest app", no_args_is_help=True
)


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


def _display_tasks(result, output_format, maniphest_instance, show_description=True):
    """Display task search/show results in the specified format.

    The switch itself lives in ``phabfive.display.display_tasks``, which is the
    canonical one; this only exists as the name the CLI call sites and their
    tests already use.
    """
    from phabfive.display import display_tasks

    display_tasks(
        result, output_format, maniphest_instance, show_description=show_description
    )


@maniphest_app.command()
def show(
    ctx: typer.Context,
    ticket_ids: List[str] = typer.Argument(
        ..., help="Task ID(s) (e.g., T123 T456 or T123,T456)"
    ),
    show_history: bool = typer.Option(
        False, "--show-history", "-H", help="Display transition history"
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", "-M", help="Display metadata about the task"
    ),
    show_comments: bool = typer.Option(
        False, "--show-comments", "-C", help="Display comments on the task"
    ),
    no_description: bool = typer.Option(
        False, "--no-description", "-n", help="Hide the task description"
    ),
) -> None:
    """Show details for one or more Maniphest tasks.

    \b
    Examples:
        phabfive maniphest show T123
        phabfive maniphest show T123 T456
        phabfive maniphest show T123,T456
        phabfive T123  # shortcut
    """
    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # Support both space-separated (T123 T456) and comma-separated (T123,T456)
    all_ids = []
    for id_arg in ticket_ids:
        all_ids.extend(part.strip() for part in id_arg.split(",") if part.strip())

    # Validate all ticket ID formats
    maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
    task_ids = []
    for ticket_id in all_ids:
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
        show_description=not no_description,
    )

    output_format = _get_output_format(ctx)
    _display_tasks(
        result, output_format, maniphest, show_description=not no_description
    )

    # A task that does not exist is a failed lookup, not an empty result.
    # Exit non-zero even when some of the requested tasks were shown, so
    # scripts can tell a partial result from a complete one.
    if result is None or result.get("missing_ids"):
        raise typer.Exit(1)


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
    title_opt: Optional[str] = typer.Option(
        None,
        "--title",
        hidden=True,
        help="Task title (hidden, use positional argument instead)",
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
        help="Add to project/workboard by name, hashtag, ID, or PHID (repeatable)",
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
        autocompletion=complete_user,
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
        autocompletion=complete_user,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Create the task in a Space (monogram, name, or unique pattern)",
        autocompletion=complete_space,
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview without creating task"
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Create without confirming",
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
    """Create a new Maniphest task.

    \b
    Examples:
        phabfive maniphest create "Fix bug"
        phabfive maniphest create "New feature" --assign=@me
        phabfive maniphest create "Task" --priority=high --tag=Sprint
        phabfive maniphest create "Task" --tag=Board --column=Backlog
        phabfive maniphest create "Task" --space=S3
        echo "Description" | phabfive maniphest create "Task" --description=-
    """
    try:
        force = resolve_assume_yes(yes, force, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    maniphest = _get_maniphest_app()

    # Merge positional and option title (positional takes precedence)
    final_title = title or title_opt

    if with_template:
        # Template mode
        try:
            result = maniphest.create_tasks_from_yaml(with_template, dry_run=dry_run)
        except PhabfiveConfigException as e:
            sys.stderr.write(f"Error: {e}\n")
            raise typer.Exit(1)
        if result and result.get("dry_run"):
            for task in result["tasks"]:
                indent = "  " * task["depth"]
                typer.echo(f"{indent}- {task['title']}")
    elif final_title:
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

            final_description = edit_text("", prefix="description-")
            if final_description and not force:
                print()
                print(final_description)
                print()
                if not typer.confirm("Create task with this description?"):
                    print("Cancelled")
                    raise typer.Exit(0)

        # Validate --column requires --tag
        if column and not tag:
            sys.stderr.write(
                "Error: --column requires --tag to specify board context\n"
            )
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

        try:
            result = maniphest.create_task(
                title=final_title,
                description=final_description,
                tags=tag,
                assignee=assign,
                status=status,
                priority=priority,
                subscribers=subscribe,
                column=column,
                board_phid=board_phid,
                space=space,
                dry_run=dry_run,
            )
        except PhabfiveConfigException as e:
            # e.g. a --tag that matches no project, or several projects
            sys.stderr.write(f"Error: {e}\n")
            raise typer.Exit(1)
        if result:
            if result.get("dry_run"):
                print("[DRY RUN] Would create task:")
                print(f"  Title: {result['title']}")
                if result.get("description"):
                    desc = result["description"]
                    lines = desc.split("\n")
                    if len(lines) == 1 and len(desc) <= 60:
                        print(f"  Description: {desc}")
                    else:
                        print("  Description:")
                        for line in lines:
                            print(f"    {line}")
                if result.get("priority"):
                    print(f"  Priority: {result['priority']}")
                if result.get("status"):
                    print(f"  Status: {result['status']}")
                if result.get("assignee"):
                    print(f"  Assignee: {result['assignee']}")
                if result.get("tags"):
                    print(f"  Tags: {', '.join(result['tags'])}")
                if result.get("column"):
                    print(f"  Column: {result['column']}")
                if result.get("subscribers"):
                    print(f"  Subscribers: {', '.join(result['subscribers'])}")
                if result.get("space"):
                    print(f"  Space: {result['space']}")
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
        help="Filter by project/workboard name, hashtag, ID, or PHID (supports wildcards)",
        autocompletion=complete_tag,
    ),
    include: Optional[str] = typer.Option(
        None,
        "--include",
        help="Force-include task(s) in results even if other filters "
        "don't match them (e.g., T123 or T123,T456)",
    ),
    exclude: Optional[str] = typer.Option(
        None,
        "--exclude",
        help="Remove task(s) from results even if the filters "
        "match them (e.g., T123 or T123,T456)",
    ),
    assigned: Optional[str] = typer.Option(
        None,
        "--assigned",
        help="Filter by assignee. Use @me for yourself.",
        autocompletion=complete_user_list_filter,
    ),
    author: Optional[str] = typer.Option(
        None,
        "--author",
        help="Filter by task author. Use @me for yourself.",
        autocompletion=complete_user_list_filter,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Filter by Space (supports wildcards)",
        autocompletion=complete_space_filter,
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
        autocompletion=complete_column_filter,
    ),
    priority: Optional[str] = typer.Option(
        None,
        "--priority",
        help="Filter tasks by priority transitions",
        autocompletion=complete_priority_filter,
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter tasks by status transitions",
        autocompletion=complete_status_filter,
    ),
    show_history: bool = typer.Option(
        False, "--show-history", help="Display transition history"
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", help="Display filter match metadata"
    ),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum results to return"),
    order: Optional[str] = typer.Option(
        None,
        "--order",
        "-o",
        help="Sort results by priority|updated|created|closed|title|relevance, "
        "optionally suffixed with :asc or :desc  [default: priority]",
        autocompletion=complete_order,
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
                "title": None,
                "description": None,
            }
        ]

    def get_param(cli_value, yaml_params, yaml_key, default=None):
        """Get value with CLI override priority."""
        if cli_value is not None:
            return cli_value
        return yaml_params.get(yaml_key, default)

    # Execute each search configuration
    output_format = _get_output_format(ctx)

    for index, config in enumerate(search_configs, start=1):
        yaml_params = config["search"]

        # Only rich and tree formats use human-facing search banners. A single
        # template needs one when its author supplied a title or description;
        # multi-document templates still need labels to separate their results.
        if output_format in ("rich", "tree") and (
            len(search_configs) > 1 or config["title"] or config["description"]
        ):
            typer.echo(f"\n{'=' * 60}")
            typer.echo(f"🔍 {config['title'] or f'Search {index}'}")
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
        final_include = get_param(include, yaml_params, "include")
        final_exclude = get_param(exclude, yaml_params, "exclude")

        def parse_task_id_list(value):
            """Parse monograms into task ID ints.

            Accepts a comma-separated string ("T123,T456") or, from YAML
            templates, a list of monograms (["T123", "T456"]).
            """
            if not value:
                return None
            raw_items = value if isinstance(value, (list, tuple)) else [value]
            maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
            task_id_list = []
            for raw_item in raw_items:
                for part in str(raw_item).split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if not re.match(maniphest_pattern, part):
                        typer.echo(
                            f"Invalid task ID '{part}'. Expected format: T123",
                            err=True,
                        )
                        raise typer.Exit(1)
                    task_id_list.append(int(part[1:]))
            return task_id_list or None

        include_task_ids = parse_task_id_list(final_include)
        exclude_task_ids = parse_task_id_list(final_exclude)

        overlap = set(include_task_ids or []) & set(exclude_task_ids or [])
        if overlap:
            overlap_str = ", ".join(f"T{tid}" for tid in sorted(overlap))
            typer.echo(f"{overlap_str} cannot be both included and excluded", err=True)
            raise typer.Exit(1)
        final_assigned = get_param(assigned, yaml_params, "assigned")
        final_author = get_param(author, yaml_params, "author")
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
        final_limit = get_param(
            limit if limit != 100 else None,
            yaml_params,
            "limit",
            100,
        )
        # Left possibly None so task_search applies the default; giving the
        # option a non-None default here would silently beat a template's
        # "order:" on every run.
        final_order = get_param(order, yaml_params, "order")

        # Check if any search criteria provided
        has_criteria = any(
            [
                final_text_query,
                final_tag,
                final_assigned,
                final_author,
                final_space,
                final_created_after,
                final_updated_after,
                column_patterns,
                priority_patterns,
                status_patterns,
                include_task_ids,
            ]
        )
        if not has_criteria:
            typer.echo("Usage:")
            typer.echo("    phabfive maniphest search [<text_query>] [options]")
            return

        try:
            result = maniphest.task_search(
                text_query=final_text_query,
                tag=final_tag,
                include_task_ids=include_task_ids,
                exclude_task_ids=exclude_task_ids,
                assigned=final_assigned,
                author=final_author,
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
                limit=final_limit,
                order=final_order,
            )
        except PhabfiveConfigException as e:
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)

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
    args: List[str] = typer.Argument(
        ...,
        metavar="TASK_IDS... [TITLE]",
        help="Task monogram(s) (e.g., T123 T124 or T123,T124,T125), "
        "optionally followed by a new title",
    ),
    title_opt: Optional[str] = typer.Option(
        None,
        "--title",
        hidden=True,
        help="New title (hidden, use positional argument instead)",
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
        help="Set assignee (username or @me for yourself)",
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
        help="Add subscriber (username or @me, repeatable)",
        autocompletion=complete_user,
    ),
    comment_text: Optional[str] = typer.Option(
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
    """Edit one or more Maniphest tasks.

    \b
    Examples:
        phabfive maniphest edit T123 "New Title"
        phabfive maniphest edit T123  # opens $EDITOR for description
        phabfive maniphest edit T123 --priority=high
        phabfive maniphest edit T123 T124 --status=resolved
        phabfive maniphest edit T123,T124 --status=resolved
        phabfive maniphest edit T123 T124 "New Title"
        phabfive maniphest edit T123 --tag="Sprint" --column=forward
        phabfive maniphest edit T123 --space=S3
    """
    # Greedy monogram parsing: leading args that are task monograms (or
    # comma-separated lists of them) are task IDs; the first non-matching
    # arg is the new title. A title that looks like a monogram must be set
    # via the hidden --title option.
    maniphest_pattern = f"^{MONOGRAMS['maniphest']}$"
    task_parts: List[str] = []
    positional_title: Optional[str] = None
    for arg in args:
        parts = [p.strip() for p in arg.split(",") if p.strip()]
        is_monograms = bool(parts) and all(
            re.match(maniphest_pattern, p) for p in parts
        )
        if is_monograms and positional_title is None:
            task_parts.extend(parts)
        elif positional_title is None:
            positional_title = arg
        else:
            typer.echo(f"Unexpected argument '{arg}'", err=True)
            raise typer.Exit(1)

    if not task_parts:
        typer.echo(
            f"Invalid task monogram '{args[0]}'. Expected format: T123", err=True
        )
        raise typer.Exit(1)

    task_ids = ",".join(task_parts)

    # Merge positional and option title (positional takes precedence)
    final_title = positional_title or title_opt

    # Delegate to Edit class for processing
    try:
        force = resolve_assume_yes(yes, force, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    edit_handler = _get_edit_app()

    retcode = edit_handler.edit_objects(
        object_id=task_ids,
        title=final_title,
        priority=priority,
        status=status,
        tag=tag,
        column=column,
        assign=assign,
        description=description,
        subscribe=subscribe,
        comment=comment_text,
        space=space,
        dry_run=dry_run,
        force=force,
        interactive=interactive,
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
