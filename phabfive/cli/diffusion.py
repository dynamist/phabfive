# -*- coding: utf-8 -*-
"""Diffusion commands for phabfive CLI."""

import sys
from typing import Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import complete_repo_status
from phabfive.constants import REPO_STATUS_CHOICES
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException

diffusion_app = typer.Typer(
    cls=AgentFooterGroup, help="The diffusion app", no_args_is_help=True
)
repo_app = typer.Typer(
    cls=AgentFooterGroup, help="Repository commands", no_args_is_help=True
)
uri_app = typer.Typer(cls=AgentFooterGroup, help="URI commands", no_args_is_help=True)
branch_app = typer.Typer(
    cls=AgentFooterGroup, help="Branch commands", no_args_is_help=True
)

diffusion_app.add_typer(repo_app, name="repo")
diffusion_app.add_typer(uri_app, name="uri")
diffusion_app.add_typer(branch_app, name="branch")


def _get_diffusion_app():
    """Get Diffusion app instance with config error handling."""
    import requests

    from phabfive.diffusion import Diffusion

    try:
        return Diffusion()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Diffusion()
    except requests.exceptions.RequestException as e:
        sys.stderr.write(f"Error: Failed to connect to Phabricator API: {e}\n")
        raise typer.Exit(1)


# Repo commands


@repo_app.command("list")
def repo_list(
    ctx: typer.Context,
    status: Optional[str] = typer.Argument(
        None,
        help="Filter by status: active, inactive, or all",
        autocompletion=complete_repo_status,
    ),
    url: bool = typer.Option(False, "--url", "-u", help="Show URL"),
) -> None:
    """List repositories."""
    diffusion = _get_diffusion_app()

    if status == "all":
        status_filter = REPO_STATUS_CHOICES
    elif status == "inactive":
        status_filter = ["inactive"]
    else:
        status_filter = ["active"]

    repos = diffusion.get_repositories_formatted(status=status_filter, include_url=url)
    for repo in repos:
        if url:
            typer.echo(", ".join(repo["urls"]))
        else:
            typer.echo(repo["name"])


@repo_app.command("create")
def repo_create(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Repository name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be created without creating it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Create without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the new repository and confirm"
    ),
) -> None:
    """Create a new repository."""
    from phabfive.editor import confirm_apply, render_changes, resolve_assume_yes

    try:
        assume_yes = resolve_assume_yes(yes, False, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    diffusion = _get_diffusion_app()

    try:
        transactions, changes = diffusion.build_repo_create(name=name)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if dry_run:
        render_changes(name, changes, header=f"[DRY RUN] Would create {name}:")
        return

    if interactive:
        render_changes(name, changes, header=f"Would create {name}:")
        confirmed, return_code = confirm_apply(assume_yes)
        if not confirmed:
            typer.echo("Nothing was created.", err=True)
            raise typer.Exit(return_code or 0)

    try:
        diffusion.apply_repo_create(transactions)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    render_changes(name, changes)


@repo_app.command("edit")
def repo_edit(
    ctx: typer.Context,
    repo: str = typer.Argument(
        ..., help="Repository monogram (R123), callsign or shortname"
    ),
    name: Optional[str] = typer.Option(
        None, "--name", help="Set the human-readable name"
    ),
    short_name: Optional[str] = typer.Option(
        None, "--short-name", help="Set the short name (rewrites built-in URIs)"
    ),
    default_branch: Optional[str] = typer.Option(
        None, "--default-branch", help="Set the default branch (e.g., main)"
    ),
    status: Optional[str] = typer.Option(
        None, "--status", help="Set status (active, inactive)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the change without making it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the change and confirm"
    ),
) -> None:
    """Edit a repository."""
    from phabfive.editor import confirm_apply, render_changes, resolve_assume_yes

    if all(arg is None for arg in [name, short_name, default_branch, status]):
        typer.echo("Please input minimum one option", err=True)
        raise typer.Exit(1)

    if status is not None and status not in REPO_STATUS_CHOICES:
        choices = ", ".join(REPO_STATUS_CHOICES)
        typer.echo(f"ERROR: --status must be one of: {choices}", err=True)
        raise typer.Exit(1)

    try:
        assume_yes = resolve_assume_yes(yes, False, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    diffusion = _get_diffusion_app()

    try:
        repo_record = diffusion.get_repo_record(repo)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    object_id = repo_record["id"]

    transactions, changes = diffusion.build_repo_edit(
        repo_record,
        name=name,
        short_name=short_name,
        default_branch=default_branch,
        status=status,
    )

    if not transactions:
        typer.echo(f"{repo}: No changes (already at target state)")
        return

    if dry_run:
        render_changes(repo, changes, header=f"[DRY RUN] Would apply to {repo}:")
        return

    if interactive:
        render_changes(repo, changes, header=f"Would apply to {repo}:")
        confirmed, return_code = confirm_apply(assume_yes)
        if not confirmed:
            typer.echo("Nothing was changed.", err=True)
            raise typer.Exit(return_code or 0)

    diffusion.apply_repo_edit(object_id, transactions)

    render_changes(repo, changes)


# URI commands


@uri_app.command("list")
def uri_list(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repository monogram (R123) or shortname"),
    clone: bool = typer.Option(False, "--clone", "-c", help="Show clone URL(s)"),
) -> None:
    """List URIs for a repository."""
    diffusion = _get_diffusion_app()
    uris = diffusion.get_uris_formatted(repo=repo, clone_uri=clone)

    # An unknown repository is a failed lookup; a repository with no URIs
    # to show is an empty result and stays successful
    if uris is None:
        typer.echo(f"Repository '{repo}' not found", err=True)
        raise typer.Exit(1)

    for uri in uris:
        typer.echo(uri)


@uri_app.command("create")
def uri_create(
    ctx: typer.Context,
    credential: str = typer.Argument(
        ..., help="SSH Private Key stored in Passphrase (e.g., K123)"
    ),
    repo: str = typer.Argument(
        ..., help="Repository monogram (R123), callsign or shortname"
    ),
    uri: str = typer.Argument(..., help="URI (e.g., git@bitbucket.org:org/repo.git)"),
    observe: bool = typer.Option(False, "--observe", help="Set I/O to observe"),
    mirror: bool = typer.Option(False, "--mirror", help="Set I/O to mirror"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be created without creating it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Create without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the change and confirm"
    ),
) -> None:
    """Create a new URI for a repository."""
    from phabfive.editor import confirm_apply, render_changes, resolve_assume_yes

    if not observe and not mirror:
        typer.echo("ERROR: Must specify either --observe or --mirror", err=True)
        raise typer.Exit(1)

    if observe and mirror:
        typer.echo("ERROR: Cannot specify both --observe and --mirror", err=True)
        raise typer.Exit(1)

    try:
        assume_yes = resolve_assume_yes(yes, False, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    if mirror:
        io = "mirror"
    else:
        io = "observe"

    diffusion = _get_diffusion_app()

    try:
        plan, changes = diffusion.build_uri_create(
            repository_name=repo,
            new_uri=uri,
            io=io,
            display="always",
            credential=credential,
        )
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if dry_run:
        render_changes(uri, changes, header=f"[DRY RUN] Would create {uri}:")
        return

    if interactive:
        render_changes(uri, changes, header=f"Would create {uri}:")
        confirmed, return_code = confirm_apply(assume_yes)
        if not confirmed:
            typer.echo("Nothing was created.", err=True)
            raise typer.Exit(return_code or 0)

    try:
        diffusion.apply_uri_create(plan)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(uri)


@uri_app.command()
def edit(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repository monogram (R123) or shortname"),
    uri: str = typer.Argument(..., help="URI to edit"),
    enable: bool = typer.Option(False, "--enable", help="Enable the URI"),
    disable: bool = typer.Option(False, "--disable", help="Disable the URI"),
    new_uri: Optional[str] = typer.Option(
        None, "--uri", help="Set the URI to this value"
    ),
    io: Optional[str] = typer.Option(
        None, "--io", help="Adjust I/O behavior (default, read, write, never)"
    ),
    display: Optional[str] = typer.Option(
        None,
        "--display",
        help="Change display behavior (default, always, hidden)",
    ),
    cred: Optional[str] = typer.Option(
        None, "--cred", help="Change credential (e.g., K2)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the change without making it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the change and confirm"
    ),
) -> None:
    """Edit a URI for a repository."""
    from phabfive.editor import confirm_apply, render_changes, resolve_assume_yes

    if enable and disable:
        typer.echo("ERROR: Cannot specify both --enable and --disable", err=True)
        raise typer.Exit(1)

    disable_flag = None
    if enable:
        disable_flag = False
    elif disable:
        disable_flag = True

    if all(arg is None for arg in [new_uri, io, display, cred, disable_flag]):
        typer.echo("Please input minimum one option", err=True)
        raise typer.Exit(1)

    try:
        assume_yes = resolve_assume_yes(yes, False, interactive)
    except ValueError as e:
        sys.stderr.write(f"Error: {e}\n")
        raise typer.Exit(1)

    diffusion = _get_diffusion_app()

    try:
        repo_record, uri_record = diffusion.get_uri_and_repo(
            repo_name=repo, uri_name=uri
        )
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    object_id = uri_record["id"]
    # A URI string does not identify a repository - two can carry the same
    # remote - so name the repository as well as the URI being changed.
    label = f"{diffusion.describe_repository(repo_record)} {uri}"

    transactions, changes = diffusion.build_uri_edit(
        uri_record,
        uri=new_uri,
        io=io,
        display=display,
        credential=cred,
        disable=disable_flag,
    )

    if not transactions:
        typer.echo(f"{label}: No changes (already at target state)")
        return

    if dry_run:
        render_changes(label, changes, header=f"[DRY RUN] Would apply to {label}:")
        return

    if interactive:
        render_changes(label, changes, header=f"Would apply to {label}:")
        confirmed, return_code = confirm_apply(assume_yes)
        if not confirmed:
            typer.echo("Nothing was changed.", err=True)
            raise typer.Exit(return_code or 0)

    diffusion.apply_uri_edit(object_id, transactions)

    render_changes(label, changes)


# Branch commands


@branch_app.command("list")
def branch_list(
    ctx: typer.Context,
    repo: str = typer.Argument(
        ..., help="Repository monogram (R123), callsign or shortname"
    ),
) -> None:
    """List branches for a repository."""
    diffusion = _get_diffusion_app()

    try:
        branches = diffusion.get_branches_formatted(repo=repo)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    for branch_name in branches:
        typer.echo(branch_name)
