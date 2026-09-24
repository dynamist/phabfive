# -*- coding: utf-8 -*-
"""The ``phabfive spec`` command group.

``spec validate`` is the one command that has to work on a machine with no
configuration at all. ``--offline`` checks a file against the generated
schema and the static semantics, which needs no token, no URL, no
``~/.arcrc`` and no network - so the app is constructed **inside the branch
that needs it**, never at the top of the function and never in a callback.
That is the whole reason this module does not route unconditionally through
``phabfive.cli.apps.get_app`` the way every other command does.

**The exit status table is not repeated here.** It is stated once, in
`phabfive.cli.spec_report`, together with the reporting that goes with it,
and is shared with ``phabfive apply`` and ``phabfive search``: a problem has
to read identically whichever of the three found it, and a second copy of
the table is how the three would drift (#486). Of the five statuses it
defines, this command uses 0 through 3; 4 is ``apply``'s alone.

What is worth saying here is why 3 exists at all, since the issue's table
had only three: a ``PhabfiveRemoteException`` out of the online pass is not
a bad spec, and reporting it as 2 would tell a deploy pipeline the spec is
broken when the network was - which is exactly the distinction the separate
codes exist for. A machine with no ``PHAB_URL`` and no ``~/.arcrc`` is the
same answer for the same reason: the offline layer passed and the online one
never ran.
"""

import enum
import os
from typing import Any, List, Optional

import typer

from phabfive.cli.agents import AgentFooterGroup
from phabfive.cli.completers import complete_spec_file
from phabfive.cli.output import _get_output_format, _setup_output_options
from phabfive.cli.spec_report import (
    CODE_UNREACHABLE,
    CODE_UNREADABLE,
    DOCUMENT,
    EXIT_CLEAN,
    EXIT_OFFLINE,
    EXIT_ONLINE,
    EXIT_UNREACHABLE,
    counts as _counts,
    parse_overrides as _parse_overrides,
    report as _report,
    undefined_variable_problem,
    unreadable_problem,
)
from phabfive.exceptions import PhabfiveException, PhabfiveRemoteException

spec_app = typer.Typer(
    cls=AgentFooterGroup,
    help="Work with Phorge spec files",
    no_args_is_help=True,
)

# The exit statuses, the two codes the command itself reports and the object
# path a document-level problem carries all live in `spec_report`, which
# `apply` and `search` share: a problem must read identically whichever
# command found it, and a copy is how the three would drift. They are
# imported back rather than re-spelled so `phabfive.cli.spec` still answers
# for every name it always has.


class Unconfigured(Exception):
    """There is no configuration to reach an instance with.

    Not a `PhabfiveException`: it never leaves this module. It exists so
    that "the instance could not be asked" has one answer whether the reason
    was a dead socket or a machine that was never told which instance to
    ask - both leave with EXIT_UNREACHABLE and one `unreachable` record.
    """


class SpecKind(str, enum.Enum):
    """What ``--kind`` accepts, which is what a spec's ``kind:`` accepts."""

    create = "create"
    search = "search"


def _online_app() -> Any:
    """Construct the app the online layer asks the instance through.

    Deliberately not `phabfive.cli.apps.get_app`, and this is the one
    command that is allowed to say so. `get_app` answers a connection
    failure with its own message and exit status 1, which is the offline
    code; this command has to tell "the spec names a user who does not
    exist" (2) apart from "the instance did not answer" (3), so the remote
    exception has to reach the caller. Everything else `get_app` does - the
    setup wizard on a missing configuration, the fallback format, the
    lookup store - comes from `new_app` and the branch below, so the only
    difference is which exception is caught where.

    Returns
    -------
    Maniphest
        A verified app, carrying the lookup store and the fallback format

    Raises
    ------
    PhabfiveRemoteException
        The instance could not be reached or refused the call. The caller
        turns this into exit status 3.
    Unconfigured
        There is no configuration to ask an instance with and the setup
        wizard was declined. The caller turns this into exit status 3 too,
        with a record: the offline layer passed and the online one never
        ran, so answering 1 would tell a CI job the spec is broken when the
        machine was, and an empty stdout is exactly what the machine formats
        exist to prevent.
    """
    from phabfive.cli.apps import new_app
    from phabfive.exceptions import PhabfiveConfigException
    from phabfive.maniphest import Maniphest

    try:
        return new_app(Maniphest)
    except PhabfiveConfigException as error:
        from phabfive.setup import offer_setup_on_error

        if not offer_setup_on_error(str(error)):
            raise Unconfigured(str(error)) from error

        return new_app(Maniphest)


@spec_app.command("validate")
def spec_validate(
    ctx: typer.Context,
    file: str = typer.Argument(
        ...,
        metavar="FILE",
        help="The spec to check: YAML, JSON, JSONL or TOML.",
        autocompletion=complete_spec_file,
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help=(
            "Check only what needs no server. No token, no URL and no "
            "network, so this runs in CI or a pre-commit hook."
        ),
    ),
    kind: Optional[SpecKind] = typer.Option(
        None,
        "--kind",
        help=(
            "What the file is, when it does not say and cannot be inferred "
            "- a search spec carrying only a title, for instance."
        ),
    ),
    variables: Optional[List[str]] = typer.Option(
        None,
        "--set",
        metavar="NAME=VALUE",
        help="Supply a variable the spec declares. Repeatable.",
    ),
) -> None:
    """Check a spec file, offline and against the instance.

    Two layers. The offline one is the schema and the static semantics: keys
    that exist, values of the right shape, variables that are declared,
    local ids that name something. The online one asks the instance whether
    every user, project, space and monogram the spec names is really there.

    Every problem is reported, not the first, so one run names everything
    that is wrong. A clean run says so.

    The online layer runs only when the offline layer found no error: there
    is no sense asking the instance about values already known to be the
    wrong shape, and the exit status could not report both anyway.

    \b
    Exit status:
        0  clean
        1  offline failure, or the file could not be read
        2  online failure: a reference does not resolve
        3  the instance could not be asked, or there is no configuration
           naming one

    \b
    Examples:
        phabfive spec validate my-spec.yaml
        phabfive spec validate my-spec.yaml --offline
        phabfive --format=json spec validate my-spec.yaml --offline
        phabfive spec validate my-spec.yaml --set sprint=42
    """
    _setup_output_options(ctx)
    output_format = _get_output_format(ctx)

    overrides = _parse_overrides(list(variables or []))

    source = os.fspath(file)
    layers = "offline" if offline else "offline and online"

    # Imported here rather than at module import: `phabfive spec --help` and
    # every other phabfive command would otherwise pay for ruamel, jinja2 and
    # the schema generator.
    from phabfive.spec import load_spec, validate_offline, validate_online
    from phabfive.spec.problems import Layer, Problem, Severity

    try:
        spec = load_spec(source, kind=kind.value if kind else None)
    except PhabfiveException as error:
        # A file that does not parse is not a Problem - there is no spec to
        # hang one on, which is why the loader raises. A --format=json
        # reader still needs a record rather than a bare status, so the
        # command makes the one record the library would not.
        _report([unreadable_problem(source, str(error))], source, layers, output_format)
        raise typer.Exit(EXIT_OFFLINE)

    problems = list(validate_offline(spec, variables=overrides))
    offline_errors, _ = _counts(problems)

    if offline or offline_errors:
        _report(problems, source, layers, output_format)
        raise typer.Exit(EXIT_OFFLINE if offline_errors else EXIT_CLEAN)

    if overrides:
        # The online layer has to see the values --set supplied, not the
        # `{{ who }}` they replace: `index_references` skips any value still
        # holding a template, so an unrendered spec would have its
        # references silently not looked up while the report said both
        # layers passed. A clean verdict for a check that never ran is the
        # one thing neither layer may produce.
        #
        # The offline pass just reported no error, so every variable
        # resolves; the guard is for the case that is a bug rather than a
        # bad spec, and it says so rather than tracebacking.
        try:
            spec = spec.render(overrides)
        except PhabfiveException as error:
            _report(
                problems + [undefined_variable_problem(str(error))],
                source,
                layers,
                output_format,
            )
            raise typer.Exit(EXIT_OFFLINE)

    # Only here. Not at the top of the function, not in a callback: --offline
    # has to work with no configuration present at all (design H.6).
    instance = None

    try:
        instance = _online_app()
        problems += validate_online(spec, instance)
    except (PhabfiveRemoteException, Unconfigured) as error:
        where = None

        if instance is not None:
            where = instance.conf.get("PHAB_URL")
        else:
            # The construction itself failed, so there is no app to ask.
            # read_config is a classmethod for exactly this: the URL without
            # validating a whole configuration.
            from phabfive.core import Phabfive

            try:
                configuration, _ = Phabfive.read_config()
                # Falsy means "not configured", which is None in a record
                # rather than an empty string that reads like a URL nobody
                # typed
                where = configuration.get("PHAB_URL") or None
            except Exception:
                # Naming the instance is a nicety; failing to is not a
                # second failure worth reporting on top of the first
                where = None

        named = f" at {where}" if where else ""
        sentence = (
            f"There is no configuration naming an instance to ask: {error}"
            if isinstance(error, Unconfigured)
            else f"The instance{named} could not be asked: {error}"
        )

        problems.append(
            Problem(
                object=DOCUMENT,
                field=None,
                value=where,
                reason=sentence,
                code=CODE_UNREACHABLE,
                layer=Layer.ONLINE,
                severity=Severity.ERROR,
            )
        )
        _report(problems, source, layers, output_format)
        raise typer.Exit(EXIT_UNREACHABLE)

    _report(problems, source, layers, output_format)

    # The offline layer reported no error to reach here, so any error left in
    # the list came from the online one.
    errors, _ = _counts(problems)

    raise typer.Exit(EXIT_ONLINE if errors else EXIT_CLEAN)


# The names lifted into `spec_report` are listed here as well: they have
# been this module's surface since #474 and are read by name, so re-exporting
# them is deliberate rather than an import that happens to be visible.
__all__ = [
    "CODE_UNREACHABLE",
    "CODE_UNREADABLE",
    "DOCUMENT",
    "EXIT_CLEAN",
    "EXIT_OFFLINE",
    "EXIT_ONLINE",
    "EXIT_UNREACHABLE",
    "SpecKind",
    "Unconfigured",
    "spec_app",
    "spec_validate",
]
