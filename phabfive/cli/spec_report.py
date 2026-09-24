# -*- coding: utf-8 -*-
"""How a spec's problems are reported, and what each exit status means.

Three commands answer for a spec file now - ``phabfive spec validate``,
``phabfive apply`` and ``phabfive search`` - and a problem has to read
identically whichever of them found it. Copying the report into each one is
how the three would drift, so it lives here once and they import it.

Nothing here decides *whether* a spec is wrong; that is
``phabfive.spec.validate`` and ``phabfive.spec.online``. This module only
turns what they answered into lines and into an exit status.

Exit status, shared by all three commands:

===  ===========================================================
  0  clean
  1  the offline layer failed, the file could not be read, or it
     is the other kind of spec
  2  the online layer failed: a reference does not resolve, or the
     instance refused what was asked
  3  the instance could not be asked, or nothing names one
  4  ``apply`` only: the run stopped partway and objects exist
===  ===========================================================

4 is the one ``spec validate`` has no use for. Conduit has no transactions,
so "nothing happened" and "half of it happened" are the two answers a caller
retrying a run most needs to tell apart, and making it read the report to
find out is making it parse prose.

Note that click exits 2 for a usage error of its own - a flag that does not
exist, a missing ``-f`` - which collides with the online code. That is not
worked around: the message says which it was.
"""

from typing import TYPE_CHECKING, Any, List, Optional, Sequence

import typer

from phabfive.constants import is_machine_format
from phabfive.json_output import emit_records

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from phabfive.spec.problems import Problem

__all__ = [
    "CODE_UNREACHABLE",
    "CODE_UNREADABLE",
    "DOCUMENT",
    "EXIT_CLEAN",
    "EXIT_OFFLINE",
    "EXIT_ONLINE",
    "EXIT_PARTIAL",
    "EXIT_UNREACHABLE",
    "clean_line",
    "counts",
    "echo_problems",
    "emit_data",
    "emit_human",
    "emit_machine",
    "grouped",
    "parse_overrides",
    "plural",
    "report",
    "report_unreadable",
    "summary",
    "undefined_variable_problem",
    "unreadable_problem",
]

# What each exit status means. Named because the tests and the docstring
# above both read them, and a bare `raise typer.Exit(2)` says nothing.
EXIT_CLEAN = 0
EXIT_OFFLINE = 1
EXIT_ONLINE = 2
EXIT_UNREACHABLE = 3
EXIT_PARTIAL = 4

# The two codes a command itself reports, which no validation layer emits:
# a file that could not be read or parsed, and an instance that could not be
# asked. Both are structural - the library raises for them rather than
# returning a Problem - but a --format=json reader must still get a record
# rather than a bare exit status, so the command builds one.
CODE_UNREADABLE = "unreadable"
CODE_UNREACHABLE = "unreachable"

# The object path problems about the document itself carry, as problems.py
# spells it.
DOCUMENT = "$"


def parse_overrides(assignments: List[str]) -> dict:
    """Read ``--set name=value`` pairs into the mapping the layers take.

    A later ``--set`` of the same name wins, which is what a caller building
    a command line up in a loop expects.

    Parameters
    ----------
    assignments : list of str
        The raw ``NAME=VALUE`` strings, in the order they were given

    Returns
    -------
    dict
        Name to value, both strings

    Raises
    ------
    typer.Exit
        A pair with no "=" in it, which is a usage mistake and not a spec
        problem. It leaves with EXIT_OFFLINE because the spec was never
        checked, so claiming it is clean would be a lie.
    """
    overrides = {}

    for assignment in assignments:
        name, separator, value = assignment.partition("=")

        if not separator or not name.strip():
            typer.echo(
                f"Error: --set expects NAME=VALUE, got {assignment!r}",
                err=True,
            )
            raise typer.Exit(EXIT_OFFLINE)

        overrides[name.strip()] = value

    return overrides


def unreadable_problem(source: str, reason: str) -> "Problem":
    """The one record a file that does not parse gets.

    A file that does not parse is not a `Problem` - there is no spec to hang
    one on, which is why the loader raises. A ``--format=json`` reader still
    needs a record rather than a bare status, so the command makes the one
    record the library would not.
    """
    from phabfive.spec.problems import Layer, Problem, Severity

    return Problem(
        object=DOCUMENT,
        field=None,
        value=source,
        reason=reason,
        code=CODE_UNREADABLE,
        layer=Layer.OFFLINE,
        severity=Severity.ERROR,
    )


def undefined_variable_problem(reason: str) -> "Problem":
    """The record a spec that could not be rendered gets.

    Reached only when the offline layer has already passed and
    `Spec.render` raises anyway, which is a bug rather than a bad spec - but
    a bug still has to be reported as the same record by every command that
    can hit it, and ``spec validate``, ``apply`` and ``search`` all can.
    ``undefined-variable`` rather than ``unreadable``: the file was read,
    and a machine reader keys on the code.
    """
    from phabfive.spec.problems import Layer, Problem, Severity

    return Problem(
        object=DOCUMENT,
        field=None,
        value=None,
        reason=reason,
        code="undefined-variable",
        layer=Layer.OFFLINE,
        severity=Severity.ERROR,
    )


def grouped(problems: Sequence["Problem"]) -> List[tuple]:
    """Problems by the object they are about, in the order they were found.

    Document order is what the layers promise and what a person reads down,
    so nothing is sorted here.

    Parameters
    ----------
    problems : sequence of Problem
        Every problem both layers reported, offline first

    Returns
    -------
    list of tuple
        (object path, list of problems), first-seen order
    """
    order: List[str] = []
    by_object: dict = {}

    for one in problems:
        if one.object not in by_object:
            order.append(one.object)
            by_object[one.object] = []
        by_object[one.object].append(one)

    return [(name, by_object[name]) for name in order]


def counts(problems: Sequence["Problem"]) -> tuple:
    """How many errors and how many warnings, as a pair."""
    from phabfive.spec.problems import Severity

    errors = sum(1 for one in problems if one.severity == Severity.ERROR)
    return errors, len(problems) - errors


def plural(count: int, noun: str) -> str:
    """One error, or two errors - the noun agreeing with the count."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def summary(problems: Sequence["Problem"], source: str) -> str:
    """The one line that says what the whole run found."""
    errors, warnings = counts(problems)

    return f"{source}: {plural(errors, 'error')}, {plural(warnings, 'warning')}"


def clean_line(source: str, layers: str) -> str:
    """What a clean run says, because saying nothing is not an answer (#395)."""
    return f"{source}: no problems found ({layers})."


def echo_problems(problems: Sequence["Problem"], *, err: bool = True) -> None:
    """The grouped lines alone, with no summary and no records.

    What `apply` and `search` print for a spec whose only problems are
    warnings: the run goes ahead, so the report must not take over stdout,
    and a `--format=json` stream must stay records of what was *done*.
    """
    from phabfive.spec.problems import Severity

    for name, group in grouped(problems):
        typer.echo(name, err=err)
        for one in group:
            where = one.field if one.field is not None else "(object)"
            mark = "" if one.severity == Severity.ERROR else " (warning)"
            typer.echo(f"  {where}: {one.reason} [{one.code}]{mark}", err=err)


def emit_human(problems: Sequence["Problem"], source: str, layers: str) -> None:
    """Print the report a person reads, grouped by object."""
    if not problems:
        typer.echo(clean_line(source, layers))
        return

    echo_problems(problems, err=False)

    typer.echo("")
    typer.echo(summary(problems, source))


def emit_data(records: Sequence[Any], output_format: str) -> None:
    """Emit already-built records in whichever machine format was asked for.

    json and jsonl go through `phabfive.json_output`, which is what keeps the
    two from drifting; yaml is the same list of records dumped as one
    document. Every machine-readable answer about a spec - problems, a plan,
    what was created - leaves through here, so the three cannot disagree
    about how a record is written.
    """
    if output_format in ("json", "jsonl"):
        emit_records(list(records), output_format)
        return

    from io import StringIO

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(list(records), stream)
    print(stream.getvalue(), end="")


def emit_machine(problems: Sequence["Problem"], output_format: str) -> None:
    """Emit one record per problem, and the same records for every format.

    An empty result is an empty list, not silence - the sentence saying so
    goes to stderr, so stdout on its own stays parseable.
    """
    emit_data([one.as_record() for one in problems], output_format)


def report(
    problems: Sequence["Problem"],
    source: str,
    layers: str,
    output_format: str,
) -> None:
    """Emit the report in whichever format was asked for.

    A machine format puts the records on stdout and the sentence about them
    on stderr, the way every other command that writes does (#344), so a
    reader piping stdout into `jq` sees records and nothing else.
    """
    if is_machine_format(output_format):
        emit_machine(problems, output_format)

        if problems:
            typer.echo(summary(problems, source), err=True)
        else:
            typer.echo(clean_line(source, layers), err=True)

        return

    emit_human(problems, source, layers)


def report_unreadable(
    source: str,
    reason: str,
    layers: str,
    output_format: str,
    problems: Optional[Sequence["Problem"]] = None,
) -> None:
    """Report a file that could not be read, as the one record it is."""
    report(
        list(problems or []) + [unreadable_problem(source, reason)],
        source,
        layers,
        output_format,
    )
