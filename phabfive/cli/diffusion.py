# -*- coding: utf-8 -*-
"""Diffusion commands for phabfive CLI."""

from typing import Optional

import typer

from phabfive.constants import REPO_STATUS_CHOICES
from phabfive.exceptions import PhabfiveConfigException

diffusion_app = typer.Typer(help="The diffusion app")
repo_app = typer.Typer(help="Repository commands")
uri_app = typer.Typer(help="URI commands")
branch_app = typer.Typer(help="Branch commands")

diffusion_app.add_typer(repo_app, name="repo")
diffusion_app.add_typer(uri_app, name="uri")
diffusion_app.add_typer(branch_app, name="branch")


def _get_diffusion_app():
    """Get Diffusion app instance with config error handling."""
    from phabfive.diffusion import Diffusion

    try:
        return Diffusion()
    except PhabfiveConfigException as e:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(e)):
            raise typer.Exit(1)
        # If setup succeeded, try again
        return Diffusion()


# Repo commands


@repo_app.command("list")
def repo_list(
    ctx: typer.Context,
    status: Optional[str] = typer.Argument(
        None,
        help="Filter by status: active, inactive, or all",
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
) -> None:
    """Create a new repository."""
    diffusion = _get_diffusion_app()
    diffusion.create_repository(name=name)


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
    for uri in uris:
        typer.echo(uri)


@uri_app.command("create")
def uri_create(
    ctx: typer.Context,
    credential: str = typer.Argument(
        ..., help="SSH Private Key stored in Passphrase (e.g., K123)"
    ),
    repo: str = typer.Argument(..., help="Repository monogram (R123) or shortname"),
    uri: str = typer.Argument(..., help="URI (e.g., git@bitbucket.org:org/repo.git)"),
    observe: bool = typer.Option(False, "--observe", help="Set I/O to observe"),
    mirror: bool = typer.Option(False, "--mirror", help="Set I/O to mirror"),
) -> None:
    """Create a new URI for a repository."""
    diffusion = _get_diffusion_app()

    if not observe and not mirror:
        typer.echo("Error: Must specify either --observe or --mirror", err=True)
        raise typer.Exit(1)

    if observe and mirror:
        typer.echo("Error: Cannot specify both --observe and --mirror", err=True)
        raise typer.Exit(1)

    if mirror:
        io = "mirror"
    else:
        io = "observe"

    created_uri = diffusion.create_uri(
        repository_name=repo,
        new_uri=uri,
        io=io,
        display="always",
        credential=credential,
    )
    typer.echo(created_uri)


@uri_app.command()
def edit(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repository monogram (R123) or shortname"),
    uri: str = typer.Argument(..., help="URI to edit"),
    enable: bool = typer.Option(False, "--enable", help="Enable the URI"),
    disable: bool = typer.Option(False, "--disable", help="Disable the URI"),
    new_uri: Optional[str] = typer.Option(None, "--new-uri", "-n", help="Change URI"),
    io: Optional[str] = typer.Option(
        None, "--io", "-i", help="Adjust I/O behavior (default, read, write, never)"
    ),
    display: Optional[str] = typer.Option(
        None,
        "--display",
        "-d",
        help="Change display behavior (default, always, hidden)",
    ),
    cred: Optional[str] = typer.Option(
        None, "--cred", "-c", help="Change credential (e.g., K2)"
    ),
) -> None:
    """Edit a URI for a repository."""
    diffusion = _get_diffusion_app()

    if enable and disable:
        typer.echo("Error: Cannot specify both --enable and --disable", err=True)
        raise typer.Exit(1)

    disable_flag = None
    if enable:
        disable_flag = False
    elif disable:
        disable_flag = True

    if all(arg is None for arg in [new_uri, io, display, cred, disable_flag]):
        typer.echo("Please input minimum one option", err=True)
        raise typer.Exit(1)

    object_id = diffusion.get_object_identifier(repo_name=repo, uri_name=uri)

    result = diffusion.edit_uri(
        uri=new_uri,
        io=io,
        display=display,
        credential=cred,
        disable=disable_flag,
        object_identifier=object_id,
    )

    if result:
        typer.echo("OK")


# Branch commands


@branch_app.command("list")
def branch_list(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repository monogram (R123) or shortname"),
) -> None:
    """List branches for a repository."""
    diffusion = _get_diffusion_app()
    branches = diffusion.get_branches_formatted(repo=repo)
    for branch_name in branches:
        typer.echo(branch_name)
