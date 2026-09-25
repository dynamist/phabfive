# -*- coding: utf-8 -*-
"""Project commands for phabfive CLI."""

import sys
from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import (
    complete_policy,
    complete_project_color,
    complete_project_status,
    complete_project_icon,
    complete_space,
    complete_space_filter,
    complete_tag,
    complete_user_list_filter,
    forget_projects,
)
from phabfive.cli.lookups import warn_unknown_project_icons
from phabfive.cli.output import (
    _echo_no_match_hint,
    _get_output_format,
    _setup_output_options,
    is_machine_format,
)
from phabfive.cli.spec_flags import with_spec_option
from phabfive.constants import (
    PROJECT_MILESTONE_ICON,
    PROJECT_ORDER_DEFAULT,
    PROJECT_ORDER_DIRECTIONS,
    PROJECT_ORDER_FIELDS,
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ANY,
    PROJECT_STATUS_CHOICES,
)
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
)
from phabfive.options import split_list_option
from phabfive.ordering import complete_order_value
from phabfive.policy import POLICY_GRAMMAR, validate_policy_value

# Every option of `project create` a spec is applied instead of: a spec is
# applied as it stands, so a value given alongside --with would be dropped
# without a word, and it is refused rather than ignored (#465). Keyed by the
# command's parameter name, valued with the option as it is typed; the same
# shape `maniphest create` and `paste create` each declare, read by the one
# `refuse_unspecced_create`.
_CREATE_OPTIONS_IGNORED_BY_SPEC = {
    "name": "NAME",
    "description": "--description",
    "icon": "--icon",
    "color": "--color",
    "slug": "--slug",
    "member": "--member",
    "parent": "--parent",
    "milestone_of": "--milestone-of",
    "space": "--space",
    "visible_to": "--visible-to",
    "editable_by": "--editable-by",
    "joinable_by": "--joinable-by",
    "yes": "--yes",
    "interactive": "--interactive",
}

project_app = typer.Typer(
    cls=AgentFooterGroup, help="The project app", no_args_is_help=True
)


def complete_project_order(incomplete: str) -> List[str]:
    """Complete `project search --order`: fields first, directions after ":"."""
    return complete_order_value(
        incomplete, PROJECT_ORDER_FIELDS, PROJECT_ORDER_DIRECTIONS
    )


def _get_project_app():
    """Get Project app instance with config error handling."""
    from phabfive.cli.apps import get_app
    from phabfive.project import Project

    return get_app(Project)


def _show_projects_after_write(ctx, project, idents):
    """Emit the records `project show` gives for the projects just written.

    A create or an edit answers a machine-readable format with exactly what
    `project show` answers with, the way every other write does (#344).
    By ID, because a rename can move the name or hashtag that found it.

    Parameters
    ----------
    ctx : typer.Context
        The command context, carrying the format the caller asked for
    project : Project
        The instance the write went through
    idents : list[str]
        Project IDs
    """
    from phabfive.project.display import display_projects

    result = project.show(idents)
    display_projects(result, _get_output_format(ctx), project)


def _validate_policies(**options):
    """Refuse a policy value outside the grammar, before the instance is reached.

    Conduit reads an unknown policy value as a policy nobody satisfies, and
    so answers a typo with a self-lockout error.
    """
    try:
        for option, value in options.items():
            validate_policy_value(value, option=f"--{option.replace('_', '-')}")
    except PhabfiveConfigException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


def _visible_to_option(what):
    return typer.Option(
        None,
        "--visible-to",
        help=f"Set who can see {what} ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    )


def _editable_by_option(what):
    return typer.Option(
        None,
        "--editable-by",
        help=f"Set who can edit {what} ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    )


def _joinable_by_option(what):
    return typer.Option(
        None,
        "--joinable-by",
        help=f"Set who can join {what} ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    )


@project_app.command("show")
def project_show(
    ctx: typer.Context,
    projects: List[str] = typer.Argument(
        ...,
        help="Project hashtag, ID, PHID or exact name (e.g., '#qa' 13 or '#qa',13)",
        autocompletion=complete_tag,
    ),
    show_policy: bool = typer.Option(
        False, "--show-policy", "-P", help="Display the project's policies"
    ),
    show_members: bool = typer.Option(
        False,
        "--show-members",
        help="Display the project's members, with their roles",
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", "-M", help="Display metadata about the project"
    ),
    no_description: bool = typer.Option(
        False, "--no-description", "-n", help="Hide the project description"
    ),
) -> None:
    """Show details for one or more projects.

    A project is named by its hashtag, its ID, its PHID or its exact name. A
    milestone has no hashtag and usually shares its name with the milestones
    of other projects, so name one by its ID - a name two projects share is
    refused, and the error lists the ID of each.

    Each member is listed with their roles as Phorge reports them - "bot",
    "admin", "disabled" and so on - which is what tells a bot account from a
    person in a script.

    \b
    Examples:
        phabfive project show '#development'
        phabfive project show 13 --show-metadata
        phabfive project show '#qa,#development'
        phabfive project show '#humans' --show-members
        phabfive --format=json project show '#qa' --show-policy
    """
    from phabfive.project.display import display_projects

    _setup_output_options(ctx)
    project = _get_project_app()

    # Space- or comma-separated, like every other show command. A hashtag
    # cannot contain a comma, so splitting on one is safe here too.
    idents = split_list_option(projects)

    try:
        result = project.show(
            idents,
            show_policy=show_policy,
            show_members=show_members,
            show_metadata=show_metadata,
            show_description=not no_description,
        )
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    display_projects(result, _get_output_format(ctx), project)

    # A project that does not exist is a failed lookup, not an empty result.
    # Exit non-zero even when some of the requested projects were shown, so
    # scripts can tell a partial result from a complete one.
    if result.get("missing_ids"):
        raise typer.Exit(1)


@project_app.command("search")
def project_search(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(
        None, help="Free text to match, the way the web UI's search box does"
    ),
    with_template: Optional[str] = with_spec_option("search"),
    member: Optional[List[str]] = typer.Option(
        None,
        "--member",
        help="Projects any of these users is a member of (@user, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list_filter,
    ),
    watcher: Optional[List[str]] = typer.Option(
        None,
        "--watcher",
        help="Projects any of these users watches, which is not the same as "
        "being a member (@user, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list_filter,
    ),
    ids: Optional[List[str]] = typer.Option(
        None,
        "--ids",
        help="Only these project IDs, as numbers, with every other filter "
        "still applied (repeatable, or comma-separated)",
    ),
    phids: Optional[List[str]] = typer.Option(
        None,
        "--phids",
        help="Only these project PHIDs, with every other filter still "
        "applied (repeatable, or comma-separated)",
    ),
    slug: Optional[List[str]] = typer.Option(
        None,
        "--slug",
        help="The projects owning these hashtags, with or without the '#' "
        "(repeatable, or comma-separated)",
        autocompletion=complete_tag,
    ),
    parent: Optional[List[str]] = typer.Option(
        None,
        "--parent",
        help="Direct subprojects and milestones of this project (repeatable)",
        autocompletion=complete_tag,
    ),
    ancestor: Optional[List[str]] = typer.Option(
        None,
        "--ancestor",
        help="Projects anywhere beneath this project (repeatable)",
        autocompletion=complete_tag,
    ),
    milestones: Optional[bool] = typer.Option(
        None,
        "--milestones/--no-milestones",
        help="Only milestones, or no milestones (default: both)",
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Which projects by status: active (default), archived, or any",
        autocompletion=complete_project_status,
    ),
    include_all: bool = typer.Option(
        False, "--all", hidden=True, help="Deprecated alias for --status=any"
    ),
    icon: Optional[List[str]] = typer.Option(
        None,
        "--icon",
        help="Projects with any of these icons (repeatable, or comma-separated)",
        autocompletion=complete_project_icon,
    ),
    color: Optional[List[str]] = typer.Option(
        None,
        "--color",
        help="Projects of any of these colors (repeatable, or comma-separated); "
        "a milestone has its parent's",
        autocompletion=complete_project_color,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Projects in these Spaces (name, monogram or pattern; comma-separated; '*' for all). Default: PHAB_SPACE",
        autocompletion=complete_space_filter,
    ),
    show_policy: bool = typer.Option(
        False, "--show-policy", "-P", help="Display each project's policies"
    ),
    show_members: bool = typer.Option(
        False,
        "--show-members",
        help="Display each project's members, with their roles",
    ),
    limit: int = typer.Option(
        100, "--limit", "-l", help="Maximum results to return, 0 for all"
    ),
    order: Optional[str] = typer.Option(
        None,
        "--order",
        "-o",
        help="Sort results by " + "|".join(PROJECT_ORDER_FIELDS) + ", optionally "
        f"suffixed with :asc or :desc  [default: {PROJECT_ORDER_DEFAULT}]",
        autocompletion=complete_project_order,
    ),
) -> None:
    """Search projects.

    With no filter at all it lists every active project in PHAB_SPACE,
    subprojects and milestones included. --status=archived lists the archived ones, and
    --status=any lists both. Like `maniphest search` it looks in PHAB_SPACE
    unless --space names other Spaces; --space='*' looks in every one.

    Naming the policies costs one extra lookup for the whole listing, not one
    per project, so an audit of every project on the instance is a single
    command.

    \b
    Examples:
        phabfive project search
        phabfive project search team
        phabfive project search --member=@me
        phabfive project search --parent='#development' --milestones
        phabfive project search --icon=group --color=red,blue
        phabfive project search --status=archived
        phabfive project search --slug=web_team
        phabfive project search --watcher=@me --order=created
        phabfive project search --with searches.yaml
        phabfive --format=jsonl project search --status=any --space='*' --show-policy -l 0
    """
    from phabfive.display import display_empty
    from phabfive.project.display import display_projects

    if status is not None and status not in PROJECT_STATUS_CHOICES:
        choices = ", ".join(PROJECT_STATUS_CHOICES)
        typer.echo(f"ERROR: --status must be one of: {choices}", err=True)
        raise typer.Exit(1)

    if include_all:
        sys.stderr.write("WARNING: --all is deprecated, use --status=any instead.\n")

        if status not in (None, PROJECT_STATUS_ANY):
            typer.echo(
                f"ERROR: --all means --status=any, and cannot be combined "
                f"with --status={status}",
                err=True,
            )
            raise typer.Exit(1)
        status = PROJECT_STATUS_ANY

    # What the command line actually carried, before the default is filled
    # in: a spec's `status:` must not be beaten by a default nobody typed.
    requested_status = status
    status = status or PROJECT_STATUS_ACTIVE

    _setup_output_options(ctx)

    # Refused rather than ignored: none of these is a search spec key yet,
    # so a spec's searches cannot carry them, and silently dropping a
    # filter would answer a narrower question than the one asked (#479).
    from phabfive.cli.search_spec import refuse_unspecced

    refuse_unspecced(
        with_template,
        {
            "--ids": ids,
            "--phids": phids,
            "--slug": slug,
            "--watcher": watcher,
            "--order": order,
        },
    )

    project = _get_project_app()

    if with_template:
        from phabfive.cli.search_spec import load_search_spec, run_search_spec

        run_search_spec(
            ctx,
            project,
            load_search_spec(with_template),
            # Keyed as a spec spells the key. None means "not given", which
            # is what keeps a flag nobody typed from clobbering the spec -
            # so `--limit 100` cannot override a spec's `limit: 5`, the same
            # quirk `maniphest search --with` has.
            overrides={
                "text_query": query,
                "members": split_list_option(member) or None,
                "parents": split_list_option(parent) or None,
                "ancestors": split_list_option(ancestor) or None,
                "milestones": milestones,
                "status": requested_status,
                "icons": split_list_option(icon) or None,
                "colors": split_list_option(color) or None,
                "spaces": split_list_option(space) or None,
                "show-policy": show_policy or None,
                "show-members": show_members or None,
                "limit": limit if limit != 100 else None,
            },
        )
        return

    # A search answers a misspelled icon with nothing, not an error
    icons = split_list_option(icon)
    warn_unknown_project_icons(project, icons, allowed=(PROJECT_MILESTONE_ICON,))

    try:
        result = project.search(
            query=query,
            ids=split_list_option(ids),
            phids=split_list_option(phids),
            slugs=split_list_option(slug),
            watchers=split_list_option(watcher),
            order=order,
            members=split_list_option(member),
            parents=split_list_option(parent),
            ancestors=split_list_option(ancestor),
            milestones=milestones,
            status=status,
            icons=icons,
            colors=split_list_option(color),
            spaces=split_list_option(space) or None,
            show_policy=show_policy,
            show_members=show_members,
            # A limit is how many projects to return, not the page size to
            # ask for, and 0 - like every other search - means every match
            limit=limit if limit > 0 else None,
        )
    # PhabfiveInputException is a PhabfiveConfigException and needs no row
    # of its own; see phabfive/exceptions.py.
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if not result.get("projects"):
        typer.echo("No projects found", err=True)
        _echo_no_match_hint(query)
        display_empty(_get_output_format(ctx))
        return

    display_projects(result, _get_output_format(ctx), project, tabular=True)


@project_app.command("create")
def project_create(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(
        None, help="Project name (required unless using --with)"
    ),
    with_template: Optional[str] = with_spec_option("create"),
    description: Optional[str] = typer.Option(
        None, "--description", help="Project description"
    ),
    icon: Optional[str] = typer.Option(
        None, "--icon", help="Icon", autocompletion=complete_project_icon
    ),
    color: Optional[str] = typer.Option(
        None, "--color", help="Color", autocompletion=complete_project_color
    ),
    slug: Optional[List[str]] = typer.Option(
        None,
        "--slug",
        help="Additional hashtag (repeatable, or comma-separated)",
    ),
    member: Optional[List[str]] = typer.Option(
        None,
        "--member",
        help="Member (@user, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list_filter,
    ),
    parent: Optional[str] = typer.Option(
        None,
        "--parent",
        help="Create it as a subproject of this project",
        autocompletion=complete_tag,
    ),
    milestone_of: Optional[str] = typer.Option(
        None,
        "--milestone-of",
        help="Create it as a milestone of this project",
        autocompletion=complete_tag,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Space to create it in (name or monogram)",
        autocompletion=complete_space,
    ),
    visible_to: Optional[str] = _visible_to_option("it"),
    editable_by: Optional[str] = _editable_by_option("it"),
    joinable_by: Optional[str] = _joinable_by_option("it"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be created without creating it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Create without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the new project and confirm"
    ),
) -> None:
    """Create a project, a subproject or a milestone.

    A name whose hashtag another project already has is refused before
    anything is sent, the way Phorge would refuse it. A project cannot be
    deleted or archived through Conduit, so check with --dry-run first.

    A milestone is named after nothing but itself - any number of them may
    share a name - and takes no --icon or --slug.

    \b
    Examples:
        phabfive project create "Platform" --icon=infrastructure --color=blue
        phabfive project create "Platform" --member=@me,@viola.larsson
        phabfive project create "Backend" --parent='#platform'
        phabfive project create "Sprint 2" --milestone-of='#platform'
        phabfive project create "Humans" --editable-by='#humans' --joinable-by=admin --dry-run
        phabfive project create --with specs/create/platform-project.yaml
    """
    from phabfive.cli.create_spec import refuse_unspecced_create
    from phabfive.cli.editor import confirm_apply, render_changes, resolve_assume_yes

    # `--with` is the deprecated spelling of `phabfive apply -f FILE`, and
    # it is the same flag on all three create commands: what a spec creates
    # is decided by the file, so this runs every object type it holds.
    # Refused before anything is constructed or connected, so a call that is
    # wrong however it resolves costs no request.
    refuse_unspecced_create(ctx, with_template, _CREATE_OPTIONS_IGNORED_BY_SPEC)

    if not with_template and not name:
        typer.echo(
            "Error: a project name is required, unless --with names a create spec.",
            err=True,
        )
        raise typer.Exit(1)

    _validate_policies(
        visible_to=visible_to, editable_by=editable_by, joinable_by=joinable_by
    )

    try:
        assume_yes = resolve_assume_yes(yes, False, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    _setup_output_options(ctx)
    project = _get_project_app()

    if with_template:
        from phabfive.cli.create_spec import load_create_spec, run_create_spec

        spec = load_create_spec(with_template)
        run_create_spec(ctx, project, spec, with_template, dry_run=dry_run)
        return

    # A machine-readable format answers with the record `project show` would
    # give. A dry run wrote nothing, so it has no record to give: the
    # preview goes to stderr and stdout stays empty.
    machine = is_machine_format(_get_output_format(ctx))
    preview = sys.stderr if machine else sys.stdout

    try:
        transactions, changes = project.build_project_create(
            name,
            description=description,
            icon=icon,
            color=color,
            slugs=split_list_option(slug),
            members=split_list_option(member),
            parent=parent,
            milestone_of=milestone_of,
            space=space,
            visible_to=visible_to,
            editable_by=editable_by,
            joinable_by=joinable_by,
        )
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if dry_run:
        # The server checks the icon of a real create; a dry run never asks it
        warn_unknown_project_icons(project, [icon] if icon else [])
        render_changes(
            name, changes, header=f"[DRY RUN] Would create {name}:", file=preview
        )
        return

    if interactive:
        render_changes(name, changes, header=f"Would create {name}:", file=preview)
        confirmed, return_code = confirm_apply(assume_yes)
        if not confirmed:
            typer.echo("Nothing was created.", err=True)
            raise typer.Exit(return_code or 0)

    try:
        created = project.apply_project_create(transactions)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    forget_projects(icons=icon is not None)

    link = f"{project.url}/project/view/{created['id']}/"
    render_changes(name, changes, header=f"Created {link}:", file=preview)

    if machine:
        _show_projects_after_write(ctx, project, [str(created["id"])])


@project_app.command("edit")
def project_edit(
    ctx: typer.Context,
    ident: str = typer.Argument(
        ...,
        metavar="PROJECT",
        help="Project hashtag, ID, PHID or exact name",
        autocompletion=complete_tag,
    ),
    name: Optional[str] = typer.Option(None, "--name", help="Rename it"),
    description: Optional[str] = typer.Option(
        None, "--description", help="Set the description"
    ),
    icon: Optional[str] = typer.Option(
        None, "--icon", help="Set the icon", autocompletion=complete_project_icon
    ),
    color: Optional[str] = typer.Option(
        None, "--color", help="Set the color", autocompletion=complete_project_color
    ),
    add_slug: Optional[List[str]] = typer.Option(
        None,
        "--add-slug",
        help="Add a hashtag (repeatable, or comma-separated)",
    ),
    join: Optional[List[str]] = typer.Option(
        None,
        "--join",
        help="Add a member (@user, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list_filter,
    ),
    add_member: Optional[List[str]] = typer.Option(
        None,
        "--add-member",
        hidden=True,
        help="Alias for --join",
        autocompletion=complete_user_list_filter,
    ),
    leave: Optional[List[str]] = typer.Option(
        None,
        "--leave",
        help="Remove a member (@user, @me or user PHID; repeatable, or comma-separated)",
        autocompletion=complete_user_list_filter,
    ),
    remove_member: Optional[List[str]] = typer.Option(
        None,
        "--remove-member",
        hidden=True,
        help="Alias for --leave",
        autocompletion=complete_user_list_filter,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Move it to this Space (name or monogram)",
        autocompletion=complete_space,
    ),
    visible_to: Optional[str] = _visible_to_option("it"),
    editable_by: Optional[str] = _editable_by_option("it"),
    joinable_by: Optional[str] = _joinable_by_option("it"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the change without making it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the change and confirm"
    ),
) -> None:
    """Edit a project.

    Only what would change is sent and listed: joining a member who is
    already in, or setting a policy the project already has, is no change.
    Adding a hashtag keeps every hashtag the project already has.

    A project cannot be archived through Conduit - Phorge offers no
    transaction for it - so that is done in the web UI.

    \b
    Examples:
        phabfive project edit '#platform' --name="Platform Team"
        phabfive project edit '#platform' --join=@viola.larsson,@mikael.wallin
        phabfive project edit '#platform' --leave=@me
        phabfive project edit '#platform' --add-slug=plat --color=green
        phabfive project edit '#humans' --editable-by='#humans' --dry-run
    """
    from phabfive.cli.editor import confirm_apply, render_changes, resolve_assume_yes

    options = [
        name,
        description,
        icon,
        color,
        add_slug,
        join,
        add_member,
        leave,
        remove_member,
        space,
        visible_to,
        editable_by,
        joinable_by,
    ]

    if all(not option for option in options):
        typer.echo("Please input minimum one option", err=True)
        raise typer.Exit(1)

    _validate_policies(
        visible_to=visible_to, editable_by=editable_by, joinable_by=joinable_by
    )

    try:
        assume_yes = resolve_assume_yes(yes, False, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    _setup_output_options(ctx)
    project = _get_project_app()

    machine = is_machine_format(_get_output_format(ctx))
    preview = sys.stderr if machine else sys.stdout

    try:
        record = project.get_project_for_edit(ident)
        transactions, changes = project.build_project_edit(
            record,
            name=name,
            description=description,
            icon=icon,
            color=color,
            add_slugs=split_list_option(add_slug),
            add_members=split_list_option([*(join or []), *(add_member or [])]),
            remove_members=split_list_option([*(leave or []), *(remove_member or [])]),
            space=space,
            visible_to=visible_to,
            editable_by=editable_by,
            joinable_by=joinable_by,
        )
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    object_id = str(record["id"])
    label = f"{project.url}/project/view/{object_id}/ ({record['fields']['name']})"

    if not transactions:
        print(f"{label}: No changes (already at target state)", file=preview)

        # "Already at the target state" is an answer about the project, not
        # an absence of one (#344).
        if machine:
            _show_projects_after_write(ctx, project, [object_id])
        return

    if dry_run:
        warn_unknown_project_icons(project, [icon] if icon else [])
        render_changes(
            label, changes, header=f"[DRY RUN] Would apply to {label}:", file=preview
        )
        return

    if interactive:
        render_changes(label, changes, header=f"Would apply to {label}:", file=preview)
        confirmed, return_code = confirm_apply(assume_yes)
        if not confirmed:
            typer.echo("Nothing was changed.", err=True)
            raise typer.Exit(return_code or 0)

    try:
        project.apply_project_edit(record["phid"], transactions)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    forget_projects(icons=icon is not None)

    render_changes(label, changes, file=preview)

    if machine:
        _show_projects_after_write(ctx, project, [object_id])
