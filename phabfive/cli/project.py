# -*- coding: utf-8 -*-
"""Project commands for phabfive CLI."""

import sys
from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import (
    complete_project_color,
    complete_project_icon,
    complete_space_filter,
    complete_tag,
    complete_user_list_filter,
)
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.constants import (
    PROJECT_STATUS_ACTIVE,
    PROJECT_STATUS_ALL,
    PROJECT_STATUS_ARCHIVED,
)
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.options import split_list_option

project_app = typer.Typer(
    cls=AgentFooterGroup, help="The project app", no_args_is_help=True
)


def _get_project_app():
    """Get Project app instance with config error handling."""
    import requests

    from phabfive.project import Project

    try:
        return Project()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Project()
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


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
    member: Optional[List[str]] = typer.Option(
        None,
        "--member",
        help="Projects any of these users is a member of (@user or @me; repeatable, or comma-separated)",
        autocompletion=complete_user_list_filter,
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
    include_all: bool = typer.Option(False, "--all", help="Include archived projects"),
    archived: bool = typer.Option(False, "--archived", help="Only archived projects"),
    icon: Optional[List[str]] = typer.Option(
        None,
        "--icon",
        help="Projects with any of these icons (repeatable, or comma-separated)",
        autocompletion=complete_project_icon,
    ),
    color: Optional[List[str]] = typer.Option(
        None,
        "--color",
        help="Projects of any of these colors (repeatable, or comma-separated)",
        autocompletion=complete_project_color,
    ),
    space: Optional[str] = typer.Option(
        None,
        "--space",
        help="Projects in these Spaces (name, monogram or pattern; comma-separated)",
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
) -> None:
    """Search projects.

    With no filter at all it lists every active project, subprojects and
    milestones included. Unlike `maniphest search` it does not narrow to
    PHAB_SPACE on its own: a Space is filtered on only when --space names
    one, so a listing is never quietly missing the projects in other Spaces.

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
        phabfive --format=jsonl project search --all --show-policy -l 0
    """
    from phabfive.project.display import display_projects

    if include_all and archived:
        typer.echo("ERROR: --all and --archived cannot be combined", err=True)
        raise typer.Exit(1)

    if include_all:
        status = PROJECT_STATUS_ALL
    elif archived:
        status = PROJECT_STATUS_ARCHIVED
    else:
        status = PROJECT_STATUS_ACTIVE

    _setup_output_options(ctx)
    project = _get_project_app()

    try:
        result = project.search(
            query=query,
            members=split_list_option(member),
            parents=split_list_option(parent),
            ancestors=split_list_option(ancestor),
            milestones=milestones,
            status=status,
            icons=split_list_option(icon),
            colors=split_list_option(color),
            spaces=split_list_option(space),
            show_policy=show_policy,
            show_members=show_members,
            # A limit is how many projects to return, not the page size to
            # ask for, and 0 - like every other search - means every match
            limit=limit if limit > 0 else None,
        )
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if not result.get("projects"):
        typer.echo("No projects found", err=True)
        return

    display_projects(result, _get_output_format(ctx), project, tabular=True)
