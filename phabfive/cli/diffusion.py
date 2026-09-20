# -*- coding: utf-8 -*-
"""Diffusion commands for phabfive CLI."""

import sys
from typing import List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import complete_policy, complete_repo_status
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.constants import REPO_STATUS_CHOICES
from phabfive.diffusion.formatters import repository_is_hosted
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.policy import POLICY_GRAMMAR, validate_policy_value

diffusion_app = typer.Typer(
    cls=AgentFooterGroup, help="The diffusion app", no_args_is_help=True
)
repo_app = typer.Typer(
    cls=AgentFooterGroup, help="Repository commands", no_args_is_help=True
)
uri_app = typer.Typer(cls=AgentFooterGroup, help="URI commands", no_args_is_help=True)

diffusion_app.add_typer(repo_app, name="repo")
diffusion_app.add_typer(uri_app, name="uri")


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


def _resolve_show_uris(show_uris: bool, url: bool) -> bool:
    """Combine --show-uris with the deprecated --url alias.

    ``--url`` printed a comma-separated line of URI strings and nothing
    else, which is not a format anything could parse. It survives as a
    hidden alias that warns, the way ``--force`` survives as an alias for
    ``--yes`` (``phabfive.editor.resolve_assume_yes``).

    Parameters
    ----------
    show_uris : bool
        Value of --show-uris
    url : bool
        Value of the hidden --url alias

    Returns
    -------
    bool
        Whether the URIs section is included
    """
    if url:
        sys.stderr.write("WARNING: --url is deprecated, use --show-uris instead.\n")

    return show_uris or url


@repo_app.command("list")
def repo_list(
    ctx: typer.Context,
    status: Optional[str] = typer.Argument(
        None,
        help="Filter by status: active, inactive, or all",
        autocompletion=complete_repo_status,
    ),
    show_uris: bool = typer.Option(
        False, "--show-uris", "-U", help="Display each repository's URIs"
    ),
    show_policy: bool = typer.Option(
        False, "--show-policy", "-P", help="Display each repository's policies"
    ),
    url: bool = typer.Option(
        False,
        "--url",
        "-u",
        hidden=True,
        help="Deprecated alias for --show-uris",
    ),
) -> None:
    """List repositories.

    Answers with the records `repo show` answers with, minus the
    per-repository sections. A URI is reported as its display URI, the one
    the web UI shows, which is what `repo show --show-uris` and `uri list`
    report too.

    Branches and tags are deliberately not offered here: each costs one
    query per repository, which a list of a large instance cannot pay.

    \b
    Examples:
        phabfive diffusion repo list
        phabfive diffusion repo list all --show-uris
        phabfive diffusion repo list --show-policy
        phabfive --format=json diffusion repo list active
    """
    from phabfive.diffusion.display import display_repositories

    _setup_output_options(ctx)
    show_uris = _resolve_show_uris(show_uris, url)
    diffusion = _get_diffusion_app()

    if status == "all":
        status_filter = REPO_STATUS_CHOICES
    elif status == "inactive":
        status_filter = ["inactive"]
    else:
        status_filter = ["active"]

    try:
        result = diffusion.repo_list(
            status=status_filter, show_uris=show_uris, show_policy=show_policy
        )
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    display_repositories(result, _get_output_format(ctx), diffusion, tabular=True)


@repo_app.command("show")
def repo_show(
    ctx: typer.Context,
    repos: List[str] = typer.Argument(
        ..., help="Repository monogram, callsign or short name (e.g., R5 R6 or R5,R6)"
    ),
    show_branches: bool = typer.Option(
        False, "--show-branches", "-B", help="Display the repository's branches"
    ),
    show_tags: bool = typer.Option(
        False, "--show-tags", "-T", help="Display the repository's tags"
    ),
    show_uris: bool = typer.Option(
        False, "--show-uris", "-U", help="Display the repository's URIs"
    ),
    show_metadata: bool = typer.Option(
        False, "--show-metadata", "-M", help="Display metadata about the repository"
    ),
    show_policy: bool = typer.Option(
        False, "--show-policy", "-P", help="Display the repository's policies"
    ),
    no_description: bool = typer.Option(
        False, "--no-description", "-n", help="Hide the repository description"
    ),
) -> None:
    """Show details for one or more repositories.

    \b
    Examples:
        phabfive diffusion repo show R5
        phabfive diffusion repo show R5 R6 --show-uris
        phabfive diffusion repo show R5,R6
        phabfive diffusion repo show R5 --show-policy
        phabfive --format=json diffusion repo show phabfive --show-branches
    """
    from phabfive.diffusion.display import display_repositories

    _setup_output_options(ctx)
    diffusion = _get_diffusion_app()

    # Support both space-separated (R5 R6) and comma-separated (R5,R6)
    repo_ids = []
    for repo_arg in repos:
        repo_ids.extend(part.strip() for part in repo_arg.split(",") if part.strip())

    try:
        result = diffusion.repo_show(
            repo_ids,
            show_branches=show_branches,
            show_tags=show_tags,
            show_uris=show_uris,
            show_metadata=show_metadata,
            show_policy=show_policy,
            show_description=not no_description,
        )
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    output_format = _get_output_format(ctx)
    display_repositories(result, output_format, diffusion)

    # A repository that does not exist is a failed lookup, not an empty
    # result. Exit non-zero even when some of the requested repositories
    # were shown, so scripts can tell a partial result from a complete one.
    if result is None or result.get("missing_ids"):
        raise typer.Exit(1)


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
    can_push: Optional[str] = typer.Option(
        None,
        "--can-push",
        help=f"Set who can push to it ({POLICY_GRAMMAR})",
        autocompletion=complete_policy,
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the change without making it"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply without confirming"),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Review the change and confirm"
    ),
) -> None:
    """Edit a repository.

    The policy options take a keyword, a #project, an @user or a PHID, and
    `--dry-run` names both ends of the change rather than showing a PHID.
    They are `--visible-to`, `--editable-by` and `--can-push`, spelled the way
    Phorge names the capability each one sets.

    A push policy on a repository Phabricator does not host is stored but
    inert. Phorge allows one to be set there and phabfive does too - a
    repository can be made hosted later - but `--can-push` says so on stderr,
    and `repo show` reports the policy as "Not a Hosted Repository" rather
    than as a value in force.
    """
    from phabfive.editor import confirm_apply, render_changes, resolve_assume_yes

    options = [
        name,
        short_name,
        default_branch,
        status,
        visible_to,
        editable_by,
        can_push,
    ]

    if all(arg is None for arg in options):
        typer.echo("Please input minimum one option", err=True)
        raise typer.Exit(1)

    if status is not None and status not in REPO_STATUS_CHOICES:
        choices = ", ".join(REPO_STATUS_CHOICES)
        typer.echo(f"ERROR: --status must be one of: {choices}", err=True)
        raise typer.Exit(1)

    # Checked before the instance is even reached, because Conduit cannot be
    # relied on to notice: it reads an unknown policy value as a policy
    # nobody satisfies, and answers a typo with a self-lockout error.
    try:
        for value, option in (
            (visible_to, "--visible-to"),
            (editable_by, "--editable-by"),
            (can_push, "--can-push"),
        ):
            validate_policy_value(value, option=option)
    except PhabfiveConfigException as e:
        typer.echo(f"ERROR: {e}", err=True)
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
    label = diffusion.link_repository(repo_record)

    # Phorge stores and edits a push policy on any repository, hosted or not,
    # so this is a warning and not a refusal - a repository that follows a
    # remote today can be made hosted tomorrow, and the policy it was given
    # takes effect then. Until it is, nothing consults it, which is why
    # Phorge's own Policies panel prints "Not a Hosted Repository" in place
    # of the value and phabfive follows it.
    if can_push is not None and not repository_is_hosted(repo_record):
        typer.echo(
            f"WARNING: {label} is not a hosted repository, so a push policy "
            "has no effect on it. It is stored, and applies if the "
            "repository becomes hosted.",
            err=True,
        )

    try:
        transactions, changes = diffusion.build_repo_edit(
            repo_record,
            name=name,
            short_name=short_name,
            default_branch=default_branch,
            status=status,
            visible_to=visible_to,
            editable_by=editable_by,
            can_push=can_push,
        )
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

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

    try:
        diffusion.apply_repo_edit(object_id, transactions)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    render_changes(label, changes)


# URI commands


def _resolve_uri_filters(io, display, builtin, external, disabled, enabled):
    """Turn the `uri list` filter options into what uri_list() takes.

    A value is validated here, before a request is made, and against the
    same constants `uri edit` validates against - there is one list of I/O
    values in phabfive, not one per command.

    Parameters
    ----------
    io, display : str or None
        The values asked for, as the caller wrote them
    builtin, external, disabled, enabled : bool
        The paired flags, at most one of each pair

    Returns
    -------
    dict
        Keyword arguments for :meth:`Diffusion.uri_list`

    Raises
    ------
    typer.Exit
        If a pair is asked for both ways, or a value is not one
    """
    from phabfive.diffusion.validators import (
        resolve_display_value,
        resolve_io_value,
    )

    if builtin and external:
        typer.echo("ERROR: Cannot specify both --builtin and --external", err=True)
        raise typer.Exit(1)

    if disabled and enabled:
        typer.echo("ERROR: Cannot specify both --disabled and --enabled", err=True)
        raise typer.Exit(1)

    try:
        return {
            "io": resolve_io_value(io) if io is not None else None,
            "display": resolve_display_value(display) if display is not None else None,
            "builtin": True if builtin else (False if external else None),
            "disabled": True if disabled else (False if enabled else None),
        }
    except PhabfiveConfigException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)


@uri_app.command("list")
def uri_list(
    ctx: typer.Context,
    repo: str = typer.Argument(..., help="Repository monogram (R123) or shortname"),
    clone: bool = typer.Option(False, "--clone", "-c", help="Show clone URL(s)"),
    io: Optional[str] = typer.Option(
        None,
        "--io",
        help="Keep the URIs with this I/O (default, observe, mirror, read, readwrite, none)",
    ),
    display: Optional[str] = typer.Option(
        None,
        "--display",
        help="Keep the URIs with this display (default, always, never)",
    ),
    builtin: bool = typer.Option(
        False, "--builtin", help="Keep only the URIs Phorge generated"
    ),
    external: bool = typer.Option(
        False, "--external", help="Keep only the URIs that were added"
    ),
    disabled: bool = typer.Option(
        False, "--disabled", help="Keep only the disabled URIs"
    ),
    enabled: bool = typer.Option(False, "--enabled", help="Keep only the enabled URIs"),
) -> None:
    """List URIs for a repository.

    One record per URI, describing all four of the dimensions a URI has:
    where it came from (`Origin`), what it does (`Role`, derived from its
    I/O), its `I/O` and `Display` behaviour, and whether it is `Disabled`.
    The URI reported is the display URI, the one the web UI shows, which
    is what `repo show --show-uris` and `repo list --show-uris` report too.

    `I/O` and `Display` are each published as `Raw`, `Default` and
    `Effective`: what is written on the URI, what it would inherit, and
    what is in force. `table` has one cell where the others have three
    levels and spells it `observe (set)` or `readwrite (default)`.

    The filters combine, and `--io` and `--display` match a value that is
    either set on the URI or in force on it - so `--io=default` finds the
    URIs that inherit their I/O, and `--io=readwrite` finds the ones that
    do read-write, however they came by it.

    A credential is named by its monogram. Its secret is never read.

    \b
    Examples:
        phabfive diffusion uri list R5
        phabfive diffusion uri list R5 --clone
        phabfive diffusion uri list R5 --io=observe
        phabfive diffusion uri list R5 --display=always --external
        phabfive diffusion uri list R5 --disabled
        phabfive --format=json diffusion uri list R5
    """
    from phabfive.diffusion.display import display_uris

    filters = _resolve_uri_filters(io, display, builtin, external, disabled, enabled)

    _setup_output_options(ctx)
    diffusion = _get_diffusion_app()

    # An unknown repository is a failed lookup; a repository with no URIs
    # to show is an empty result and stays successful
    try:
        result = diffusion.uri_list(repo, clone_only=clone, **filters)
    except PhabfiveDataException as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    display_uris(result, _get_output_format(ctx), diffusion, tabular=True)


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
        None,
        "--io",
        help="Adjust I/O behavior (default, observe, mirror, read, readwrite, none)",
    ),
    display: Optional[str] = typer.Option(
        None,
        "--display",
        help="Change display behavior (default, always, never)",
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
    label = diffusion.link_repository(repo_record)

    try:
        transactions, changes = diffusion.build_uri_edit(
            uri_record,
            uri=new_uri,
            io=io,
            display=display,
            credential=cred,
            disable=disable_flag,
        )
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    if not transactions:
        typer.echo(f"{label}: No changes (already at target state)")
        return

    # An edit that does not touch the URI - a --disable, say - would
    # otherwise never say which URI on the repository it meant.
    if not any(change["field"] == "URI" for change in changes):
        changes = [{"field": "URI", "old": None, "new": uri}] + changes

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
