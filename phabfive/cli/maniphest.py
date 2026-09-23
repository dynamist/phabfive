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
    complete_policy,
    complete_priority,
    complete_priority_change,
    complete_priority_filter,
    complete_space,
    complete_space_filter,
    complete_status,
    complete_status_filter,
    complete_tag,
    complete_tag_list,
    complete_user,
    complete_user_list,
    complete_user_list_filter,
)
from phabfive.cli.output import (
    _echo_no_match_hint,
    _exit_with_help,
    _get_output_format,
    _setup_output_options,
    is_machine_format,
)
from phabfive.constants import MONOGRAMS
from phabfive.cli.editor import resolve_assume_yes
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.options import split_list_option
from phabfive.policy import POLICY_GRAMMAR

maniphest_app = typer.Typer(
    cls=AgentFooterGroup, help="The maniphest app", no_args_is_help=True
)


def _get_maniphest_app():
    """Get Maniphest app instance with config error handling."""
    from phabfive.cli.apps import get_app
    from phabfive.maniphest import Maniphest

    return get_app(Maniphest)


def _split_values_to_add(values, option):
    """Split a list option of ``maniphest create``, still accepting ``+``.

    ``create --tag`` and ``--subscribe`` once split on ``+`` alone, which is
    the AND of the search-filter grammar and means nothing in a list of
    values to add. They take commas now, like every other list option, and
    ``+`` keeps working with a warning so that no script breaks on upgrade.

    Parameters
    ----------
    values : list or None
        What the option collected, one string per occurrence
    option : str
        The option's name, for the warning

    Returns
    -------
    list
        The values, as ``split_list_option`` returns them
    """
    if values and any("+" in value for value in values):
        sys.stderr.write(
            f"WARNING: '+' between {option} values is deprecated, use ',' instead.\n"
        )
        values = [value.replace("+", ",") for value in values]

    return split_list_option(values)


# Every option of `maniphest create` that the template path cannot honour: the
# template is applied as it stands, so a value given alongside --with would be
# dropped without a word. Threading them through is #481; until then they are
# refused rather than ignored (#465). Keyed by the command's parameter name,
# valued with the option as it is typed.
_CREATE_OPTIONS_IGNORED_BY_TEMPLATE = {
    "title": "TITLE",
    "title_opt": "--title",
    "description": "--description",
    "tag": "--tag",
    "column": "--column",
    "assign": "--assign",
    "status": "--status",
    "priority": "--priority",
    "subscribe": "--subscribe",
    "space": "--space",
    "visible_to": "--visible-to",
    "editable_by": "--editable-by",
    "yes": "--yes",
    "interactive": "--interactive",
    "force": "--force",
}


def _options_given(ctx, options):
    """Which of ``options`` the command line actually carried.

    Typer has no unset sentinel - an option left out and one passed the value
    it defaults to look exactly alike - so click's record of where each value
    came from is what answers "was this passed".

    Parameters
    ----------
    ctx : typer.Context
        The command context
    options : dict
        Parameter name to the option as it is typed

    Returns
    -------
    list
        The options given, in the order ``options`` lists them
    """
    from click.core import ParameterSource

    return [
        option
        for name, option in options.items()
        if ctx.get_parameter_source(name) not in (None, ParameterSource.DEFAULT)
    ]


def _display_tasks(
    result, output_format, maniphest_instance, show_description=True, tabular=False
):
    """Display task search/show results in the specified format.

    The switch itself lives in ``phabfive.display.display_tasks``, which is the
    canonical one; this only exists as the name the CLI call sites and their
    tests already use.
    """
    from phabfive.display import display_tasks

    display_tasks(
        result,
        output_format,
        maniphest_instance,
        show_description=show_description,
        tabular=tabular,
    )


def _show_tasks_after_write(ctx, maniphest_instance, task_ids):
    """Emit the records `show` gives for the tasks a write command touched.

    A create, an edit or a comment answers a machine-readable format with
    exactly what ``maniphest show`` answers with for the object it just
    wrote, so the monogram, the link and every field arrive together and no
    second, parallel "result" shape has to be invented or kept in step.

    Parameters
    ----------
    ctx : typer.Context
        The command context, carrying the format the caller asked for
    maniphest_instance : Maniphest
        The instance the write went through
    task_ids : list
        Task IDs, numeric or as strings
    """
    result = maniphest_instance.task_show([int(task_id) for task_id in task_ids])
    _display_tasks(result, _get_output_format(ctx), maniphest_instance)


def _report_partial_create(report, output_format):
    """Say what exists after a create spec failed partway through.

    Creation is one call per object and Conduit has no transactions, so
    "it failed" is not an answer when the objects before the failure are
    still there. Every object gets a record - ``created``, ``failed`` or
    ``skipped`` - so the run can be cleaned up or resumed by name rather
    than by reading back the log (#485).

    A machine format puts the records on stdout and the sentence about
    them on stderr, the way every other command that writes does (#344),
    so a reader piping stdout into ``jq`` sees records and nothing else.
    A human format writes the lot to stderr: this is what went wrong, and
    a real run has never printed anything to stdout.

    Parameters
    ----------
    report : phabfive.spec.create.CreateReport
        Every record of the run, in the order the objects were attempted
    output_format : str
        The format the caller asked for
    """
    if is_machine_format(output_format):
        records = report.as_records()

        if output_format == "yaml":
            from io import StringIO

            from ruamel.yaml import YAML

            yaml = YAML()
            yaml.default_flow_style = False
            stream = StringIO()
            yaml.dump(records, stream)
            print(stream.getvalue(), end="")
        else:
            from phabfive.json_output import emit_records

            emit_records(records, output_format)

        sys.stderr.write(f"Error: {report.summary}\n")
        return

    sys.stderr.write(f"Error: {report.summary}\n")

    for record in report.records:
        shown = record.monogram or record.phid
        suffix = f" {shown}" if shown else ""

        if record.reason and record.status == "failed":
            suffix = f"{suffix}: {record.reason}"

        sys.stderr.write(f"  {record.status:<8} {record.label}{suffix}\n")


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
    show_policy: bool = typer.Option(
        False, "--show-policy", "-P", help="Display the task's policies"
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
        phabfive maniphest show T123 --show-policy
        phabfive T123  # shortcut
    """
    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # Support both space-separated (T123 T456) and comma-separated (T123,T456)
    all_ids: list[str] = []
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
        show_policy=show_policy,
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
    """Add a comment to a Maniphest task.

    A machine-readable format answers with the task's record, the one
    `maniphest show` gives, rather than with the link on its own.
    """
    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    maniphest.add_task_comment(ticket_id, comment_text)

    task_id = int(ticket_id[1:])

    if is_machine_format(_get_output_format(ctx)):
        _show_tasks_after_write(ctx, maniphest, [task_id])
        return

    # Query the ticket to fetch the URI for it
    ticket = maniphest.get_task_info(task_id)
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
        help="Add to project/workboard by name, hashtag, ID, or PHID "
        "(repeatable, comma-separated)",
        autocompletion=complete_tag_list,
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
        help="Set assignee (username, @me for yourself, or a user PHID)",
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
        help="Add subscriber (username, @me or user PHID, repeatable, comma-separated)",
        autocompletion=complete_user_list,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Create the task in a Space (monogram, name, or unique pattern)",
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
        phabfive maniphest create "Task" --tag=Sprint,QA --subscribe=@me,alice
        phabfive maniphest create "Task" --tag=Board --column=Backlog
        phabfive maniphest create "Task" --space=S3
        phabfive maniphest create "Task" --visible-to='#infra' --editable-by=admin
        echo "Description" | phabfive maniphest create "Task" --description=-
    """
    # A template is applied as it stands, so every value the template path
    # cannot honour used to be accepted and then dropped in silence. Refuse it
    # instead, and before anything is constructed or connected: nothing a
    # caller asked for should go missing without a word (#465).
    if with_template:
        ignored = _options_given(ctx, _CREATE_OPTIONS_IGNORED_BY_TEMPLATE)

        if ignored:
            sys.stderr.write(
                f"Error: --with cannot be combined with {', '.join(ignored)}; "
                "a creation template is applied as it stands. Put the values "
                "in the template, or create the task without --with.\n"
            )
            raise typer.Exit(1)

    try:
        force = resolve_assume_yes(yes, force, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    # A machine-readable format answers with the record `show` would give
    # for the task that was created. There is no such record for a task
    # that was only previewed, so a dry run writes its preview to stderr
    # and leaves stdout empty rather than putting prose in a JSON stream.
    output_format = _get_output_format(ctx)
    machine = is_machine_format(output_format)
    preview = sys.stderr if machine else sys.stdout

    # Merge positional and option title (positional takes precedence)
    final_title = title or title_opt

    if with_template:
        # Template mode. Imported here rather than at module level so that
        # `phabfive T123` does not pay for the spec engine, and statically
        # enough for PyInstaller to follow - `_LAZY` is what it cannot see.
        from phabfive.spec.create import CreateFailed

        try:
            result = maniphest.create_tasks_from_yaml(with_template, dry_run=dry_run)
        except CreateFailed as partial:
            # Conduit has no transactions, so a template that failed on its
            # fiftieth object left forty-nine behind. Answering with one
            # sentence would leave the caller to find out which from the
            # log; the records say it per object (#485).
            _report_partial_create(partial.report, output_format)
            raise typer.Exit(1)
        except (PhabfiveConfigException, PhabfiveDataException) as e:
            sys.stderr.write(f"Error: {e}\n")
            raise typer.Exit(1)
        if result and result.get("dry_run"):
            # The command says so, not the library: `phabfive.spec.create`
            # builds a plan and never writes, and a log.warning from inside
            # it was a user message in the wrong place
            print("[DRY RUN] Would create:", file=preview)
            for task in result["tasks"]:
                indent = "  " * task["depth"]
                print(f"{indent}- {task['title']}", file=preview)
                if task.get("assignee"):
                    print(f"{indent}  Assignee: {task['assignee']}", file=preview)
                if task.get("subscribers"):
                    subscribers = ", ".join(task["subscribers"])
                    print(f"{indent}  Subscribers: {subscribers}", file=preview)
        elif machine and result and result.get("task_ids"):
            # One query for the whole template, and the same records
            # `maniphest show` gives - a tree of tasks is still just tasks.
            _show_tasks_after_write(ctx, maniphest, result["task_ids"])
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
            from phabfive.cli.editor import edit_text

            final_description = edit_text("", prefix="description-")
            if final_description and not force:
                print(file=preview)
                print(final_description, file=preview)
                print(file=preview)
                # The prompt follows the preview onto stderr under a machine
                # format, so that answering it cannot land in the stream the
                # caller is parsing.
                if not typer.confirm("Create task with this description?", err=machine):
                    print("Cancelled", file=preview)
                    raise typer.Exit(0)

        tags = _split_values_to_add(tag, "--tag")
        subscribers = _split_values_to_add(subscribe, "--subscribe")

        # Validate --column requires --tag
        if column and not tags:
            sys.stderr.write(
                "Error: --column requires --tag to specify board context\n"
            )
            raise typer.Exit(1)

        # Resolve board PHID if column is specified
        board_phid = None
        if column and tags:
            # Use the first tag as the board context
            board_phids = maniphest._resolve_project_phids(tags[0])
            if board_phids:
                board_phid = board_phids[0]
            else:
                sys.stderr.write(f"Error: Board not found: {tags[0]}\n")
                raise typer.Exit(1)

        try:
            result = maniphest.create_task(
                title=final_title,
                description=final_description,
                tags=tags,
                assignee=assign,
                status=status,
                priority=priority,
                subscribers=subscribers,
                column=column,
                board_phid=board_phid,
                space=space,
                visible_to=visible_to,
                editable_by=editable_by,
                dry_run=dry_run,
            )
        except (PhabfiveConfigException, PhabfiveDataException) as e:
            # e.g. a --tag that matches no project, or several projects, or a
            # policy naming a project or user that does not exist
            sys.stderr.write(f"Error: {e}\n")
            raise typer.Exit(1)
        if result:
            if result.get("dry_run"):
                print("[DRY RUN] Would create task:", file=preview)
                print(f"  Title: {result['title']}", file=preview)
                if result.get("description"):
                    desc = result["description"]
                    lines = desc.split("\n")
                    if len(lines) == 1 and len(desc) <= 60:
                        print(f"  Description: {desc}", file=preview)
                    else:
                        print("  Description:", file=preview)
                        for line in lines:
                            print(f"    {line}", file=preview)
                if result.get("priority"):
                    print(f"  Priority: {result['priority']}", file=preview)
                if result.get("status"):
                    print(f"  Status: {result['status']}", file=preview)
                if result.get("assignee"):
                    print(f"  Assignee: {result['assignee']}", file=preview)
                if result.get("tags"):
                    print(f"  Tags: {', '.join(result['tags'])}", file=preview)
                if result.get("column"):
                    print(f"  Column: {result['column']}", file=preview)
                if result.get("subscribers"):
                    print(
                        f"  Subscribers: {', '.join(result['subscribers'])}",
                        file=preview,
                    )
                if result.get("space"):
                    print(f"  Space: {result['space']}", file=preview)
                for label, value in (result.get("policy") or {}).items():
                    print(f"  {label}: {value}", file=preview)
            elif machine:
                _show_tasks_after_write(ctx, maniphest, [result["id"]])
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
        help="Filter by assignee: username, @me for yourself, or user PHID",
        autocompletion=complete_user_list_filter,
    ),
    author: Optional[str] = typer.Option(
        None,
        "--author",
        help="Filter by task author: username, @me for yourself, or user PHID",
        autocompletion=complete_user_list_filter,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Filter by Space (supports wildcards)",
        autocompletion=complete_space_filter,
    ),
    visible_to: Optional[str] = typer.Option(
        None,
        "--visible-to",
        help=f"Only tasks whose view policy is exactly this ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    ),
    editable_by: Optional[str] = typer.Option(
        None,
        "--editable-by",
        help=f"Only tasks whose edit policy is exactly this ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
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
    closed_after: Optional[str] = typer.Option(
        None, "--closed-after", help="Tasks closed within TIME (e.g., 1h, 7d, 2w)"
    ),
    closed_before: Optional[str] = typer.Option(
        None, "--closed-before", help="Tasks closed more than TIME ago"
    ),
    closed_by: Optional[str] = typer.Option(
        None,
        "--closed-by",
        help="Filter by who closed the task: username, @me for yourself, or user PHID",
        autocompletion=complete_user_list_filter,
    ),
    ids: Optional[str] = typer.Option(
        None,
        "--ids",
        help="Only these tasks, with the other filters still applied "
        "(e.g., T123 or T123,T456). Use --include to bypass the filters",
    ),
    phids: Optional[str] = typer.Option(
        None,
        "--phids",
        help="Only these task PHIDs, with the other filters still applied",
    ),
    subscriber: Optional[str] = typer.Option(
        None,
        "--subscriber",
        help="Filter by subscriber: username, @me for yourself, or user PHID",
        autocompletion=complete_user_list_filter,
    ),
    subtype: Optional[str] = typer.Option(
        None,
        "--subtype",
        help="Filter by task subtype key, e.g. default",
    ),
    parent: Optional[str] = typer.Option(
        None,
        "--parent",
        help="Only the subtasks of these tasks (e.g., T123 or T123,T456)",
    ),
    subtask: Optional[str] = typer.Option(
        None,
        "--subtask",
        help="Only the parents of these tasks (e.g., T123 or T123,T456)",
    ),
    # Optional[bool] rather than bool: a flag nobody typed has to stay
    # distinguishable from --has-parents meaning "no", the way every other
    # override here does. Only the True half has a flag; a template says
    # `has-parents: false` for the other one.
    has_parents: Optional[bool] = typer.Option(
        None,
        "--has-parents",
        help="Only tasks that are a subtask of something",
    ),
    has_subtasks: Optional[bool] = typer.Option(
        None,
        "--has-subtasks",
        help="Only tasks that have subtasks",
    ),
    include_all: bool = typer.Option(
        False,
        "--all",
        hidden=True,
        help="Deprecated: use --status=any",
    ),
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
        help="Filter by status: open (default), closed, any, or transition "
        "patterns; AND them with + (e.g. any+in:Resolved)",
        autocompletion=complete_status_filter,
    ),
    show_history: bool = typer.Option(
        False, "--show-history", help="Display transition history"
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", help="Display filter match metadata"
    ),
    show_policy: bool = typer.Option(
        False, "--show-policy", help="Display each task's policies"
    ),
    limit: int = typer.Option(
        100, "--limit", "-l", help="Maximum results to return, 0 for all"
    ),
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
    from phabfive.spec.search import (
        SearchPlanError,
        banner_title,
        plan_search,
        run_search,
        wants_banner,
    )

    _setup_output_options(ctx)
    maniphest = _get_maniphest_app()

    if with_template:
        try:
            search_items = maniphest._load_search_config(with_template)
        except Exception as e:
            typer.echo(f"ERROR: Failed to load template file: {e}", err=True)
            raise typer.Exit(1)
    else:
        # No template is one unconstrained search, which the criteria guard
        # below then answers with the help unless the command line supplied
        # something to search for.
        search_items = [
            {
                "search": {},
                "title": None,
                "description": None,
            }
        ]

    # Only what the command line actually carried, keyed as a spec spells it.
    # Typer has no unset marker: a boolean flag that was not given and one
    # that was given as false are the same value, and `--limit 100` cannot be
    # told from the default. Passing None for those is what keeps a flag
    # nobody typed from clobbering a template's value - a quirk, since
    # `--limit 100` therefore cannot override a template's `limit: 5`, and one
    # that is kept deliberately: click's parameter source is the correct fix
    # and it would change behaviour, so it is its own issue.
    overrides = {
        "text_query": text_query,
        "tag": tag,
        "include": include,
        "exclude": exclude,
        "assigned": assigned,
        "author": author,
        "space": space,
        "visible-to": visible_to,
        "editable-by": editable_by,
        "created-after": created_after,
        "created-before": created_before,
        "updated-after": updated_after,
        "updated-before": updated_before,
        "closed-after": closed_after,
        "closed-before": closed_before,
        "closed-by": closed_by,
        "ids": ids,
        "phids": phids,
        "subscriber": subscriber,
        "subtype": subtype,
        "parent": parent,
        "subtask": subtask,
        # Already None when the flag was not given, which is what the
        # sentinel above spells out longhand for the older boolean flags.
        "has-parents": has_parents,
        "has-subtasks": has_subtasks,
        "column": column,
        "priority": priority,
        "status": status,
        "show-history": show_history if show_history else None,
        "show-metadata": show_metadata if show_metadata else None,
        "show-policy": show_policy if show_policy else None,
        "all": include_all if include_all else None,
        "limit": limit if limit != 100 else None,
        "order": order,
    }

    output_format = _get_output_format(ctx)
    total = len(search_items)

    # One item at a time, all the way through. Planning every search up front
    # would move a later search's failure ahead of an earlier search's
    # results, which is a visible change to what a person sees; a program
    # that wants the atomic report calls `plan_searches` instead.
    for index, item in enumerate(search_items, start=1):
        # Only rich and tree formats use human-facing search banners. A single
        # template needs one when its author supplied a title or description;
        # multi-document templates still need labels to separate their results.
        if output_format in ("rich", "tree") and wants_banner(
            item["title"], item["description"], total
        ):
            typer.echo(f"\n{'=' * 60}")
            typer.echo(f"🔍 {banner_title(item['title'], index)}")
            if item["description"]:
                typer.echo(f"📝 {item['description']}")
            typer.echo(f"{'=' * 60}")

        try:
            plan = plan_search(
                maniphest,
                item,
                overrides=overrides,
                index=index,
                total=total,
            )
        except SearchPlanError as e:
            # A task id that is not one, and an id in both lists, are
            # sentences of their own and have never carried the prefix.
            if e.check in ("invalid-task-id", "include-exclude-overlap"):
                typer.echo(str(e), err=True)
            elif e.check == "unsupported-type":
                # `maniphest search` searches tasks. A spec item naming
                # another object type is refused rather than run as a task
                # search, and the sentence names a command that does run it.
                typer.echo(
                    f"ERROR: {banner_title(item['title'], index)}: "
                    f"'maniphest search' runs a task search, and this one is "
                    f"a {e.object_type!r} search. Run the spec from "
                    f"'phabfive {e.object_type} search --with' instead, "
                    f"which runs every type a spec holds.",
                    err=True,
                )
            else:
                typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)
        except (PhabfiveConfigException, PhabfiveDataException) as e:
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)

        # The deprecation stays here rather than in the planner: both
        # sentences name command-line spellings, and which of the two is
        # printed depends on where the value came from, which only the
        # command knows. The planner answers whether the scopes contradict.
        if plan.params["include_closed"]:
            # --all only ever lifted the open-only default, which is what
            # --status=any says without promising "every task" (#419).
            if include_all:
                typer.echo(
                    "WARNING: --all is deprecated, use --status=any instead.", err=True
                )
            else:
                typer.echo(
                    "WARNING: 'all: true' in a search template is deprecated, "
                    "use 'status: any' instead.",
                    err=True,
                )
            conflicting = plan.conflicting_status_scopes
            if conflicting:
                typer.echo(
                    f"ERROR: --all cannot be combined with --status "
                    f"{'/'.join(conflicting)}; use --status alone",
                    err=True,
                )
                raise typer.Exit(1)

        # A bare "search" still prints help rather than querying the whole
        # instance; --status=any is how a script asks for every task on
        # purpose, and the deprecated --all still counts as the same request.
        if not plan.has_criteria:
            _exit_with_help(ctx)

        try:
            result = run_search(maniphest, plan)
        except (PhabfiveConfigException, PhabfiveDataException) as e:
            # A policy value outside the grammar is a config error, a project
            # or user it names that does not exist a data error; both are
            # found before anything is fetched
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)

        _display_tasks(result.payload, output_format, maniphest, tabular=True)

        # An empty search printed nothing at all. When it searched for text,
        # say why that may be, since the likeliest reason is a part of a word
        # given to a search that matches whole words.
        if plan.text_query and not (result.payload or {}).get("tasks"):
            typer.echo("No tasks found", err=True)
            _echo_no_match_hint(plan.text_query)


def _get_edit_app():
    """Get Edit app instance with config error handling."""
    from phabfive.cli.apps import get_app
    from phabfive.edit import Edit

    return get_app(Edit)


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
        phabfive maniphest edit T123 --visible-to=public --editable-by='#infra'
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

    _setup_output_options(ctx)
    edit_handler = _get_edit_app()

    # Imported here: it pulls in the edit planning, which a command that is
    # only completing or printing help should not pay for
    from phabfive.cli import edit_flow

    retcode = edit_flow.run_edit(
        edit_handler,
        object_id=task_ids,
        output_format=_get_output_format(ctx),
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
        visible_to=visible_to,
        editable_by=editable_by,
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
    _display_tasks(result, output_format, maniphest, tabular=True)


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
    _display_tasks(result, output_format, maniphest, tabular=True)
