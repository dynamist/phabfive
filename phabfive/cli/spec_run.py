# -*- coding: utf-8 -*-
"""``phabfive apply -f`` and ``phabfive search -f``: running a spec, whole.

A spec creating a project and the tasks tagged into it does not belong under
``maniphest create``, and that is the whole reason these two commands are at
the top level: the verb is what makes "all the object types in one file"
reachable. Everything they can create or search is already reachable from a
library - :mod:`phabfive.create` and :mod:`phabfive.search` dispatch on the
object type - so what is added here is a terminal and an exit status.

Both commands are the same ladder with opposite kinds, which is why they
share a module: load, dispatch on ``kind:``, check, and only then do
anything.

1. **Load with no kind.** `phabfive.cli.spec_flags.load_of_kind` says why;
   in short, telling the loader the kind makes it reinterpret the file
   rather than refuse it.
2. **Dispatch.** A search spec handed to ``apply`` is answered with one
   sentence naming ``phabfive search -f``, and the mirror for the other
   direction. It is a usage refusal, not a validation problem: it never
   enters a ``--format=json`` problem stream.
3. **Both layers, and neither is skipped.** The offline layer runs here; the
   online one runs inside the planner, which is what resolves every name to
   a PHID. An error in either refuses the run.
4. **Then, and only then**, the plan is applied or the searches are run.

``--dry-run`` stops after step 3 with the plan printed. There is no
confirmation prompt: ``maniphest create --with`` has never had one, the spec
file *is* the statement of intent, ``--dry-run`` is the preview, and
prompting would put a terminal in the one path programs use.

Exit status is `phabfive.cli.spec_report`'s, shared with ``spec validate``,
plus 4 for a run that stopped partway with objects already created.
"""

import os
from typing import Any, List, Optional

import typer

from phabfive.cli.completers import complete_spec_file
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.cli.spec_flags import dispatch_kind, load_of_kind
from phabfive.cli.spec_report import (
    EXIT_CLEAN,
    EXIT_OFFLINE,
    EXIT_ONLINE,
    EXIT_PARTIAL,
    EXIT_UNREACHABLE,
    counts,
    echo_problems,
    emit_data,
    parse_overrides,
    report,
    report_unreadable,
    undefined_variable_problem,
)
from phabfive.constants import is_machine_format
from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveException,
    PhabfiveRemoteException,
)

__all__ = ["apply_command", "search_command"]

#: Both layers run for both commands, so the line a report ends with says so.
LAYERS = "offline and online"


def _instance() -> Any:
    """The app both commands ask the instance through.

    `phabfive.cli.apps.get_app` like every other command, and deliberately
    **not** the offline-first construction ``spec validate`` does: that
    command has to work on a machine that has never seen a token, and these
    two cannot do anything useful without one, so failing fast is right.

    What is different is only the status. `get_app` has already said what
    went wrong and leaves with 1, which in this ladder means "the spec is
    wrong". Neither a declined setup wizard nor a host that does not answer
    is a bad spec, so both leave with 3 instead - the same distinction
    ``spec validate`` draws, for the same reason.
    """
    from phabfive.cli.apps import get_app
    from phabfive.maniphest import Maniphest

    try:
        return get_app(Maniphest)
    except typer.Exit as leaving:
        if leaving.exit_code == EXIT_CLEAN:
            raise

        raise typer.Exit(EXIT_UNREACHABLE) from None
    except PhabfiveRemoteException as error:
        typer.echo(f"Error: the instance could not be asked: {error}", err=True)
        raise typer.Exit(EXIT_UNREACHABLE) from None
    except PhabfiveConfigException as error:
        # The setup wizard ran and the configuration is still not one that
        # names an instance. Nothing about the spec is known to be wrong.
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(EXIT_UNREACHABLE) from None


def _loaded(source: str, wanted: str, output_format: str) -> Any:
    """The spec, or the report of why it could not be read, and exit 1."""
    try:
        spec = load_of_kind(source, wanted)
    except PhabfiveException as error:
        report_unreadable(source, str(error), LAYERS, output_format)
        raise typer.Exit(EXIT_OFFLINE) from None

    dispatch_kind(spec, wanted, source)

    return spec


def _checked(spec: Any, source: str, overrides: dict, output_format: str) -> Any:
    """Run the offline layer, refuse an error, and render the variables.

    A clean run says nothing at all here, unlike ``spec validate``: these
    commands answer with what they *did*, and a "no problems found" line
    ahead of that would be noise in a terminal and a second kind of record
    in a stream. Warnings are printed - on stderr, always as text, whatever
    ``--format`` says - because the run goes ahead despite them and stdout
    belongs to the result.
    """
    from phabfive.spec import validate_offline

    problems = list(validate_offline(spec, variables=overrides))
    errors, warnings = counts(problems)

    if errors:
        report(problems, source, LAYERS, output_format)
        raise typer.Exit(EXIT_OFFLINE)

    if warnings:
        echo_problems(problems)

    # Rendering is skipped only when there is nothing at all to render with.
    # `spec.variables` alone is the wrong question: an override for a name the
    # document never declares is still applied - that is how a spec may read
    # `{{ sprint }}` and leave supplying it to whoever runs it - so a spec with
    # an empty `variables:` and a `--set sprint=42` would otherwise be planned
    # with the template still in the title. `spec validate` guards on the same
    # pair, and the two commands must not disagree about one file.
    if not spec.variables and not overrides:
        return spec

    # The online pass must see the values `--set` supplied, not the `{{ who }}`
    # they replace: a value still holding a template names nothing an
    # instance could be asked about. The offline pass just reported no error,
    # so every variable resolves; this guard is for the case that is a bug
    # rather than a bad spec, and it says so rather than tracebacking.
    try:
        return spec.render(overrides)
    except PhabfiveException as error:
        report([undefined_variable_problem(str(error))], source, LAYERS, output_format)
        raise typer.Exit(EXIT_OFFLINE) from None


def _item_line(item: Any) -> str:
    """One line of a dry run's tree, indented by where the item was written.

    An anchor creates nothing: it is an object that already exists, read for
    its PHID so the items nested under it can hang off it. *Which* object
    that is, is the one thing a reader of the preview needs - a spec with two
    anchors would otherwise print two identical lines - and an anchor has no
    title of its own to say it with, so the monograms it was written as are
    named instead.
    """
    named = f"${item.local_id}" if item.local_id else None
    anchors = ", ".join(item.display.get("anchors") or []) or None
    title = f"{item.title!r}" if item.title else None
    what = " ".join(part for part in (item.object_type, named, anchors, title) if part)

    marker = "- " if item.creates else "  (existing) "

    return f"{'  ' * (item.depth + 1)}{marker}{what}"


def _counted(plan: Any) -> str:
    """ "1 project, 3 tasks" - what the run would leave behind."""
    from phabfive.cli.spec_report import plural

    counted = plan.counts()

    if not counted:
        return "nothing"

    return ", ".join(
        plural(counted[object_type], object_type) for object_type in sorted(counted)
    )


def _preview(plan: Any, source: str, output_format: str) -> None:
    """Print the plan and write nothing.

    A machine format gets the plan's own records, which is what makes a
    dry run something a program can act on rather than prose it has to
    parse; the sentence about them goes to stderr like every other record
    stream's does.
    """
    sentence = f"{source}: would create {_counted(plan)}."

    if is_machine_format(output_format):
        emit_data(plan.as_records(), output_format)
        typer.echo(sentence, err=True)
        return

    typer.echo(f"[DRY RUN] {sentence}")

    for item in plan.items:
        typer.echo(_item_line(item))


def _record_line(record: Any) -> str:
    """One line of the running commentary, per object, as it happens.

    `CreateRecord.label` names an object by the `$local-id` the spec gave it
    where it has one, which is the right name for a sentence about the
    *spec*. A person watching a run needs the monogram too - it is what they
    have to type next - so both are said here, and the label is the fallback
    for an object the spec neither named nor titled.
    """
    named = [
        part
        for part in (
            f"${record.local_id}" if record.local_id else None,
            record.monogram,
        )
        if part
    ]

    if named:
        parts = [record.object_type, *named]

        if record.title:
            parts.append(f"{record.title!r}")

        what = " ".join(parts)
    else:
        what = record.label

    line = f"{record.status:<8} {what}"

    if record.reason and record.status == "failed":
        line = f"{line}: {record.reason}"

    return line


def apply_command(
    ctx: typer.Context,
    spec_file: str = typer.Option(
        ...,
        "-f",
        "--spec",
        metavar="FILE",
        help="The create spec to apply: YAML, JSON, JSONL or TOML.",
        autocompletion=complete_spec_file,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would be created and write nothing.",
    ),
    variables: Optional[List[str]] = typer.Option(
        None,
        "--set",
        metavar="NAME=VALUE",
        help="Supply a variable the spec declares. Repeatable.",
    ),
) -> None:
    """Create everything a spec describes, whatever object types it holds.

    One file can create a project, a milestone of it and the tasks tagged
    into it, linked to each other by the `$local-id`s the spec gives them.
    Every name in it is resolved before anything is written, so a spec whose
    tenth task names a user who does not exist creates nothing at all.

    A search spec is refused by name, with the command that runs it.

    \b
    Exit status (`phabfive.cli.spec_report` is where the table is defined):
        0  everything was created, or --dry-run planned cleanly
        1  the file could not be read, is a search spec, or the offline
           layer found an error
        2  the online layer failed: a reference does not resolve, or the
           instance refused what was asked
        3  the instance could not be asked, or nothing names one
        4  the run stopped partway and objects exist
    A usage mistake - an unknown flag, a missing -f - also leaves with 2,
    which is click's own; the message says which it was.

    \b
    Examples:
        phabfive apply -f specs/create/platform-bootstrap.yaml --dry-run
        phabfive apply -f specs/create/sprint-tasks.yaml --set sprint=42
        phabfive --format=json apply -f specs/create/sprint-tasks.yaml
    """
    _setup_output_options(ctx)
    output_format = _get_output_format(ctx)
    machine = is_machine_format(output_format)

    overrides = parse_overrides(list(variables or []))
    source = os.fspath(spec_file)

    spec = _loaded(source, "create", output_format)
    spec = _checked(spec, source, overrides, output_format)

    # Imported here rather than at module import: every other phabfive
    # command would otherwise pay for the spec engine, and statically enough
    # for PyInstaller to follow.
    from phabfive.create import apply_plan, plan_spec
    from phabfive.spec.create import CreatePlanError, CreateReport

    instance = _instance()

    try:
        plan = plan_spec(instance, spec)
    except CreatePlanError as error:
        # What the document holds, not what the instance said: a spec naming
        # an object type nothing creates is wrong in the file.
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(EXIT_OFFLINE) from None
    except PhabfiveRemoteException as error:
        typer.echo(f"Error: the instance could not be asked: {error}", err=True)
        raise typer.Exit(EXIT_UNREACHABLE) from None
    except (PhabfiveConfigException, PhabfiveDataException) as error:
        # The offline layer passed, so everything the planner refuses here is
        # the instance's answer: a user nobody has, a column no board has, a
        # priority this instance does not define.
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(EXIT_ONLINE) from None

    if dry_run:
        _preview(plan, source, output_format)
        raise typer.Exit(EXIT_CLEAN)

    # Flushed per record, so a reader downstream sees each object as it is
    # created rather than when the run ends - which for a partial failure is
    # the difference between knowing what exists and waiting for a run that
    # has already stopped.
    from phabfive.json_output import emit_record

    records = []

    for record in apply_plan(instance, plan):
        records.append(record)

        if output_format == "jsonl":
            emit_record(record.as_record(), "jsonl")
        elif not machine:
            typer.echo(_record_line(record))

    done = CreateReport(records=tuple(records))

    if machine and output_format != "jsonl":
        emit_data(done.as_records(), output_format)

    if done.ok:
        typer.echo(done.summary, err=machine)
        raise typer.Exit(EXIT_CLEAN)

    # The per-object records are already out, in whichever format was asked
    # for; this is the one sentence about them, and it goes to stderr so a
    # reader piping stdout into `jq` still sees records and nothing else.
    typer.echo(f"Error: {done.summary}", err=True)

    # Conduit has no transactions, so "nothing happened" and "half of it
    # happened" are the two answers a caller retrying most needs to tell
    # apart. 4 says objects exist; 2 says the instance refused and nothing
    # does.
    raise typer.Exit(EXIT_PARTIAL if done.created else EXIT_ONLINE)


def search_command(
    ctx: typer.Context,
    spec_file: str = typer.Option(
        ...,
        "-f",
        "--spec",
        metavar="FILE",
        help="The search spec to run: YAML, JSON, JSONL or TOML.",
        autocompletion=complete_spec_file,
    ),
    variables: Optional[List[str]] = typer.Option(
        None,
        "--set",
        metavar="NAME=VALUE",
        help="Supply a variable the spec declares. Repeatable.",
    ),
) -> None:
    """Run every search a spec holds, whatever object types it searches.

    One file can hold a task search, a project search and a paste search,
    and each is run and printed in document order through one client.

    A create spec is refused by name, with the command that runs it.

    \b
    Exit status (`phabfive.cli.spec_report` is where the table is defined):
        0  every search ran; an empty result is still 0
        1  the file could not be read, is a create spec, the offline layer
           found an error, or a search item names no filter at all
        2  the online layer failed: a reference does not resolve, or the
           instance refused what was asked
        3  the instance could not be asked, or nothing names one
    A usage mistake - an unknown flag, a missing -f - also leaves with 2,
    which is click's own; the message says which it was.

    \b
    Examples:
        phabfive search -f specs/search/release-readiness.yaml
        phabfive search -f specs/search/high-priority-stale-tasks.yaml --set stale_days=30
        phabfive --format=json search -f specs/search/active-projects.yaml
    """
    _setup_output_options(ctx)
    output_format = _get_output_format(ctx)

    overrides = parse_overrides(list(variables or []))
    source = os.fspath(spec_file)

    spec = _loaded(source, "search", output_format)
    spec = _checked(spec, source, overrides, output_format)

    from phabfive.cli.search_spec import run_search_spec

    instance = _instance()

    # `run_search_spec` is shared with the deprecated `--with` path, where
    # every failure is 1. Here the offline layer has already passed, so what
    # the *instance* refuses - a reference that does not resolve, a search it
    # would not run - is 2, while what the *file* gets wrong stays 1. The
    # loop knows which of the two it caught; a blanket remap around this call
    # would not, and would answer 2 for a search item that names no filter,
    # which no instance was ever asked about.
    run_search_spec(ctx, instance, spec, online_exit=EXIT_ONLINE)
