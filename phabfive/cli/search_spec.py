# -*- coding: utf-8 -*-
"""`--with`: the command that runs a search spec, whatever it searches.

One ingestion point for `project search`, `paste search` and `passphrase
search`, so a spec holding several object types runs each of them in
document order, through one app and one client::

    kind: search
    searches:
      - type: project
        search: {status: active, members: ["@me"]}
      - type: paste
        search: {author: "@me"}
      - type: passphrase
        search: {type: key}

Everything that needs a terminal is here and nowhere else: the banner
between two searches, the exit status, the "nothing found" line on stderr
and the hint after a text search that matched nothing. What a spec *means*
is `phabfive.search.dispatch`, and what the format is is `phabfive.spec`.
"""

from typing import Any, Mapping, Optional

import typer

from phabfive.cli.output import (
    _echo_no_match_hint,
    _get_output_format,
)
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)
from phabfive.search import (
    app_for,
    has_criteria,
    plan_item,
    records_of,
    run_item,
    searched_text,
)
from phabfive.spec.envelope import DEFAULT_SEARCH_TYPE
from phabfive.spec.search import SearchPlanError, banner_title, wants_banner

__all__ = ["load_search_spec", "refuse_unspecced", "run_search_spec"]


#: What a search that found nothing says, per object type. On stderr, so a
#: program reading stdout still sees an empty result. A task search says
#: nothing at all unless it searched for text, which is what `maniphest
#: search` has always done.
_NOTHING_FOUND = {
    "task": "No tasks found",
    "project": "No projects found",
    "paste": "No pastes found",
    "passphrase": "No credentials found matching the criteria",
}

#: What a search with no filters at all is answered with. `project search`
#: is absent on purpose: listing every active project is a request, so the
#: project runner declares no criteria and `has_criteria` is always true for
#: it. Read through :data:`_NO_CRITERIA_DEFAULT`, never indexed: the day the
#: project runner is given a criterion, an absent row must be a sentence and
#: not a KeyError.
_NO_CRITERIA = {
    "task": "a task search needs at least one filter",
    "paste": "a paste search needs a text query or an author",
    "passphrase": "a passphrase search needs a text query or a type",
}

#: For an object type that has criteria and no sentence of its own yet.
_NO_CRITERIA_DEFAULT = "this search needs at least one filter"


def refuse_unspecced(with_template, options):
    """Refuse an option a spec's searches cannot carry, rather than drop it.

    `--with` runs each search from the spec, and only the keys
    `phabfive.search.dispatch` declares reach it. An option that is not one
    of those keys yet would be read, accepted and then quietly ignored,
    which answers a different question than the one asked - so the command
    says so and stops.

    One copy for every command that takes `--with`, so the three of them
    cannot drift on the sentence or on the exit status.

    Parameters
    ----------
    with_template : str or None
        What --with carried, if anything
    options : dict
        Flag name to the value the command line gave it

    Raises
    ------
    typer.Exit
        Status 1, naming every offending flag at once
    """
    if not with_template:
        return

    given = [flag for flag, value in options.items() if value]

    if given:
        typer.echo(
            f"ERROR: {', '.join(given)} cannot be combined with --with; "
            "a search spec has no key for them yet. Run the search without "
            "--with, or put the filter in the spec.",
            err=True,
        )
        raise typer.Exit(1)


def load_search_spec(path: str):
    """Read a search spec, or leave with the message and exit status 1.

    Variables are rendered here, because a value still holding ``{{ who }}``
    names nothing an instance could be asked about. A spec that declares one
    nothing supplies is an error, not a search.

    **The kind is not handed to the loader.** It used to be, and an explicit
    kind beats both a declared ``kind:`` and inference - so a create spec
    given to ``project|paste|passphrase search --with`` was not refused, it
    was reinterpreted, down to an invented empty search item that ran
    unconstrained. It is loaded for what it is and refused by name (#486).

    Every command that reaches this function reached it through ``--with``,
    which is why the deprecation warning is here: three call sites cannot
    drift on the sentence if there is one place that says it.
    """
    from phabfive.cli.spec_flags import (
        dispatch_kind,
        load_of_kind,
        warn_with_deprecated,
    )

    warn_with_deprecated("phabfive search -f FILE")

    try:
        spec = load_of_kind(path, "search")
    # PhabfiveInputException is a PhabfiveConfigException; see
    # phabfive/exceptions.py.
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: Failed to load template file: {e}", err=True)
        raise typer.Exit(1)

    dispatch_kind(spec, "search", path)

    # Inside the same handler as the load: a variable that does not resolve is
    # as much "this file could not be read" as a parse error, and answering it
    # with `cli_entrypoint`'s generic line instead would make the deprecated
    # path say two different things about one file.
    try:
        return spec.render() if spec.variables else spec
    except (PhabfiveConfigException, PhabfiveDataException) as e:
        typer.echo(f"ERROR: Failed to load template file: {e}", err=True)
        raise typer.Exit(1)


def _banner(plan_title, description, index, output_format) -> None:
    """The separator between one search's results and the next.

    Only the human formats get one: a machine format's reader would have to
    parse it back out again.
    """
    if output_format not in ("rich", "tree"):
        return

    typer.echo(f"\n{'=' * 60}")
    typer.echo(f"🔍 {banner_title(plan_title, index)}")
    if description:
        typer.echo(f"📝 {description}")
    typer.echo(f"{'=' * 60}")


def _before_run(app: Any, plan) -> None:
    """The one warning a search prints before it runs.

    A misspelled icon is answered by Phorge with an empty result rather than
    an error, so `project search` says so itself - and a spec's icons get
    the same warning the command line's do.
    """
    if plan.object_type != "project":
        return

    from phabfive.cli.lookups import warn_unknown_project_icons
    from phabfive.constants import PROJECT_MILESTONE_ICON

    warn_unknown_project_icons(
        app, plan.params.get("icons") or [], allowed=(PROJECT_MILESTONE_ICON,)
    )


def _display(result, output_format: str, app: Any) -> None:
    """Print one search's records, in that object type's own shape.

    Each type keeps the display its own `show` command uses, so a filter
    written against `project show` works on a spec's project search too.
    """
    object_type = result.plan.object_type
    payload = result.payload

    if object_type == "task":
        from phabfive.cli.maniphest import _display_tasks

        _display_tasks(payload, output_format, app, tabular=True)
    elif object_type == "project":
        from phabfive.project.display import display_projects

        display_projects(payload, output_format, app, tabular=True)
    elif object_type == "paste":
        from phabfive.paste.display import display_pastes

        display_pastes(payload, output_format, app, tabular=True)
    else:
        from phabfive.passphrase.display import display_passphrases_list

        # show_secrets is False by construction: a search spec never fetches
        # secret material, see `phabfive.search.dispatch`.
        display_passphrases_list(payload or [], output_format, app, show_secrets=False)


def run_search_spec(
    ctx: typer.Context,
    parent: Any,
    spec,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    online_exit: int = 1,
) -> None:
    """Run every search a spec holds and print each one's results.

    One item at a time, all the way through: a spec's second search is
    planned only after the first one's results have been printed, so what a
    person sees is in document order even when a later search is the one
    that fails. `phabfive.search.run_spec` is the same loop without a
    terminal, for a program.

    Parameters
    ----------
    ctx : typer.Context
        The command's context, carrying the format asked for.
    parent : Phabfive
        The app the command built. Every other object type's app is built
        from it, so one configuration and one client serve them all.
    spec : Spec
        A loaded search spec.
    overrides : mapping, optional
        What the command line carried, keyed as a spec spells it. A value of
        None counts as not supplied.
    online_exit : int, optional
        The status to leave with when the *instance* is what refused: a
        reference that does not resolve, a search Conduit would not run.
        Defaults to 1, which is what the deprecated ``--with`` path has
        always answered with and is frozen at. ``phabfive search -f`` passes
        2, because it has already run the offline layer itself and so can
        tell a file that is wrong from an instance that said no - which is
        the one behavioural difference between the two entry points.

    Raises
    ------
    typer.Exit
        Status 1 for a spec that cannot be planned - a search item with no
        filter at all, or one the planner refuses by shape - and
        ``online_exit`` for what the instance refused.
    """
    output_format = _get_output_format(ctx)
    items = spec.items("search")
    total = len(items)
    apps: dict[str, Any] = {}

    for index, item in enumerate(items, start=1):
        object_type = str(item.get("type") or DEFAULT_SEARCH_TYPE)

        # Printed before the item is planned, so a search whose filter does
        # not parse still says which search it was.
        if wants_banner(item.get("title"), item.get("description"), total):
            _banner(item.get("title"), item.get("description"), index, output_format)

        try:
            if object_type not in apps:
                apps[object_type] = app_for(object_type, parent)

            app = apps[object_type]
            plan = plan_item(
                app,
                item,
                overrides=overrides,
                index=index,
                total=total,
                source=spec.source,
            )
        except SearchPlanError as e:
            # The document's own fault: a key this object type has no filter
            # for, a pattern that does not parse. No instance was involved.
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(1)
        except (PhabfiveConfigException, PhabfiveDataException) as e:
            # Planning is also where every name is resolved, so this is the
            # instance answering: a user nobody has, a column no board has.
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(online_exit)

        if not has_criteria(plan):
            typer.echo(
                f"ERROR: {banner_title(plan.title, index)}: "
                f"{_NO_CRITERIA.get(object_type, _NO_CRITERIA_DEFAULT)}",
                err=True,
            )
            # Also the document's fault: an item that names no filter at all
            # would search the whole instance.
            raise typer.Exit(1)

        _before_run(app, plan)

        try:
            result = run_item(app, plan)
        except (
            PhabfiveConfigException,
            PhabfiveDataException,
            PhabfiveRemoteException,
        ) as e:
            typer.echo(f"ERROR: {e}", err=True)
            raise typer.Exit(online_exit)

        _display(result, output_format, app)

        if records_of(result):
            continue

        message = _NOTHING_FOUND.get(object_type, "Nothing found")
        text = searched_text(plan)

        # A task search prints nothing for an empty result unless it
        # searched for text, which is the one case where the likeliest
        # reason - a part of a word given to a search that matches whole
        # ones - is worth saying.
        if message and (object_type != "task" or text):
            typer.echo(message, err=True)

        if text:
            _echo_no_match_hint(text)
