# -*- coding: utf-8 -*-
"""`--with`: the command that runs a create spec, whatever it creates.

The create half of `phabfive.cli.search_spec`, and it exists for the same
reason: one ingestion point, so the commands that take ``--with`` cannot
drift on what a file means, what a wrong file is answered with, or what a
run prints.

``--with`` is the **deprecated spelling of** ``phabfive apply -f FILE``, and
this module is what makes that sentence true rather than approximately true.
Every ``--with`` create site loads through :func:`load_create_spec` and runs
through :func:`run_create_spec`, which is the same ladder ``apply -f`` walks
- one plan for the whole document, one preview, one record per object - so
the three commands are three names for one behaviour::

    phabfive maniphest create --with sprint.yaml
    phabfive project create   --with sprint.yaml
    phabfive paste create     --with sprint.yaml
    phabfive apply -f         sprint.yaml        # the form that stays

What each of them creates is decided by the **file**, never by which command
was typed: a create spec is a document about several object types, and
``phabfive.create.dispatch`` sends each item to the app that creates it. So
``paste create --with`` on a file holding a project and three tasks creates
the project and the three tasks, exactly as ``apply -f`` would. A command
that ran only its own section would silently drop the rest, which is the
class of loss this whole phase exists to close.

Why ``--with`` was added to ``project create`` and ``paste create`` at all,
given that it is deprecated: it was on ``maniphest create`` alone while
`phabfive.spec.create.CREATABLE_TYPES` holds three object types, so the flag
read as "maniphest can be driven by a file and the other two cannot", which
was never true of the format and is not true of the planner. A deprecated
flag that is on one of three sibling commands is a worse thing to find than
a deprecated flag that is on all three.

Exit status is 1 for everything, which is what every other ``--with`` site
answers with. The graded table - 2 for the online layer, 3 for an instance
that cannot be asked, 4 for a partial run - is `phabfive.cli.spec_report`'s
and belongs to ``apply -f``, which is the command a caller who wants it
should be running.

Nothing here decides *what* a spec means: that is `phabfive.spec`, and what
creates each object type is `phabfive.create`.
"""

from typing import Any, Optional

import typer

from phabfive.cli.output import _get_output_format
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)

__all__ = [
    "load_create_spec",
    "options_given",
    "refuse_unspecced_create",
    "run_create_spec",
]


def options_given(ctx: typer.Context, options: dict) -> list:
    """Which of ``options`` the command line actually carried.

    Typer has no unset sentinel - an option left out and one passed the
    value it defaults to look exactly alike - so click's record of where
    each value came from is what answers "was this passed".

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


def refuse_unspecced_create(
    ctx: typer.Context, with_template: Optional[str], options: dict
) -> None:
    """Refuse an option a create spec's items cannot carry, rather than drop it.

    The create twin of `phabfive.cli.search_spec.refuse_unspecced`, and the
    same rule: a spec is applied as it stands, so an option that the file
    has no place for would be accepted and then silently ignored - which
    answers a different question than the one asked (#465).

    One copy for every command that takes ``--with``, so they cannot drift
    on the sentence or on the exit status. Checked **before** anything is
    constructed or connected, so a call that is wrong however it resolves
    costs no request.

    Parameters
    ----------
    ctx : typer.Context
        The command context, for what the command line actually carried
    with_template : str or None
        What ``--with`` carried, if anything
    options : dict
        Parameter name to the option as it is typed

    Raises
    ------
    typer.Exit
        Status 1, naming every offending flag at once
    """
    if not with_template:
        return

    given = options_given(ctx, options)

    if given:
        typer.echo(
            f"Error: --with cannot be combined with {', '.join(given)}; "
            "a creation spec is applied as it stands. Put the values "
            "in the spec, or create without --with.",
            err=True,
        )
        raise typer.Exit(1)


def load_create_spec(path: str) -> Any:
    """Read a create spec, or leave with the message and exit status 1.

    The create twin of `phabfive.cli.search_spec.load_search_spec`, down to
    the sentence a file that cannot be read is answered with, and it does
    the same three things in the same order.

    **The kind is not handed to the loader.** An explicit kind beats both a
    declared ``kind:`` and inference, so a search spec given to a ``create
    --with`` would be reinterpreted rather than refused. It is loaded for
    what it is and refused by name, with the command that does run it.

    Variables are rendered here, because a value still holding ``{{ who }}``
    names nothing an instance could be asked about. A spec that declares one
    nothing supplies is an error, not a create.

    Every command that reaches this function reached it through ``--with``,
    which is why the deprecation warning is here: one place says it, so no
    number of call sites can drift on the sentence.
    """
    from phabfive.cli.spec_flags import (
        dispatch_kind,
        load_of_kind,
        warn_with_deprecated,
    )

    warn_with_deprecated("phabfive apply -f FILE")

    try:
        spec = load_of_kind(path, "create")
    # PhabfiveInputException is a PhabfiveConfigException; see
    # phabfive/exceptions.py.
    except (PhabfiveConfigException, PhabfiveDataException) as error:
        typer.echo(f"ERROR: Failed to load template file: {error}", err=True)
        raise typer.Exit(1) from None

    dispatch_kind(spec, "create", path)

    # Inside the same handler as the load: a variable that does not resolve
    # is as much "this file could not be read" as a parse error, and
    # answering it with `cli_entrypoint`'s generic line instead would make
    # the deprecated path say two different things about one file.
    try:
        return spec.render()
    except (PhabfiveConfigException, PhabfiveDataException) as error:
        typer.echo(f"ERROR: Failed to load template file: {error}", err=True)
        raise typer.Exit(1) from None


def run_create_spec(
    ctx: typer.Context, app: Any, spec: Any, source: str, *, dry_run: bool = False
) -> None:
    """Plan a create spec, then preview it or apply it.

    The same ladder `phabfive.cli.spec_run.apply_command` walks, and
    deliberately the same functions: the preview, the per-object line and
    the closing sentence are imported from there rather than written again,
    so ``--with`` and ``-f`` cannot come to describe one run differently.

    What is *not* shared is the exit status. ``apply -f`` grades a failure -
    2 for the online layer, 4 for a run that stopped with objects already
    created - and every ``--with`` site in the tree answers 1 for
    everything. A caller who wants the graded answer is being told, once per
    run, which command gives it.

    Parameters
    ----------
    ctx : typer.Context
        For the output format only; `_setup_output_options` has already run
        in the command.
    app : Phabfive
        An already-constructed app. Which class it is does not decide what
        is created: `phabfive.create.dispatch` sends each item to the app
        that creates that object type, sharing this one's client.
    spec : Spec
        Already loaded and rendered, by :func:`load_create_spec`.
    source : str
        The path, as the caller typed it, for the sentences.
    dry_run : bool
        Print the plan and write nothing.

    Raises
    ------
    typer.Exit
        Status 1 if anything was refused. A clean run returns.
    """
    from phabfive.cli.spec_run import _preview, _record_line
    from phabfive.constants import is_machine_format
    from phabfive.create import apply_plan, plan_spec
    from phabfive.json_output import emit_record
    from phabfive.spec.create import CreatePlanError, CreateReport

    output_format = _get_output_format(ctx)
    machine = is_machine_format(output_format)

    try:
        plan = plan_spec(app, spec)
    except CreatePlanError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from None
    except PhabfiveRemoteException as error:
        typer.echo(f"Error: the instance could not be asked: {error}", err=True)
        raise typer.Exit(1) from None
    except (PhabfiveConfigException, PhabfiveDataException) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from None

    if dry_run:
        _preview(plan, source, output_format)
        return

    records = []

    for record in apply_plan(app, plan):
        records.append(record)

        if output_format == "jsonl":
            emit_record(record.as_record(), "jsonl")
        elif not machine:
            typer.echo(_record_line(record))

    done = CreateReport(records=tuple(records))

    if machine and output_format != "jsonl":
        from phabfive.cli.spec_report import emit_data

        emit_data(done.as_records(), output_format)

    if done.ok:
        typer.echo(done.summary, err=machine)
        return

    # The per-object records are already out; this is the one sentence about
    # them, on stderr so a reader piping stdout into `jq` still sees records
    # and nothing else.
    typer.echo(f"Error: {done.summary}", err=True)
    raise typer.Exit(1)
