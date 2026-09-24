# -*- coding: utf-8 -*-
"""Reading a spec the caller has not said the kind of, and refusing the wrong one.

``phabfive apply`` creates and ``phabfive search`` searches, so each of them
knows which kind of spec it is willing to run - and that is exactly the
knowledge it must **not** hand to the loader.

``load_spec(path, kind="create")`` does not refuse a search spec: an explicit
kind beats both a declared ``kind:`` and inference, so the file is
*reinterpreted*. Handed a create spec, ``kind="search"`` even invents an
empty search item, and an unconstrained search runs where the caller asked
for nothing of the sort. That was live on ``project|paste|passphrase search
--with`` until #486.

So a command loads with no kind at all, and compares afterwards::

    spec = load_of_kind(path, "create")     # the loader decides
    dispatch_kind(spec, "create", path)     # the command refuses, by name

The refusal is a **usage refusal, not a validation problem**: it is one
sentence on stderr in every format, and it never enters a ``--format=json``
problem stream. A spec that is the other kind is not a spec with something
wrong in it; it is a spec run by the wrong command, and the sentence says
which command to run instead.
"""

import logging
from typing import TYPE_CHECKING, Any

import typer

from phabfive.cli.spec_report import EXIT_OFFLINE

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from phabfive.spec.envelope import Spec

log = logging.getLogger(__name__)

__all__ = [
    "COMMAND_OF_KIND",
    "dispatch_kind",
    "load_of_kind",
    "warn_with_deprecated",
    "with_spec_option",
]


#: What runs each kind of spec, as a person would type it. One table, so the
#: refusal, the deprecation warning and any later command cannot drift on
#: what the new form is called.
COMMAND_OF_KIND = {
    "create": "phabfive apply -f",
    "search": "phabfive search -f",
}

#: The sentence that differs between the two kinds, and the only part of
#: `--with`'s help that may. A create spec is applied whole - combining it
#: with another option is refused rather than half-honoured - while a search
#: spec is the starting point and the options beside it narrow the result.
#: Both are behaviours the commands actually have; see `tests/test_with_option_parity.py`.
_WITH_BEHAVIOUR = {
    "create": "Other options on this command are refused alongside it.",
    "search": "Every other option on this command overrides what the spec says.",
}

#: What `--with` reads, said once. `apply -f` and `search -f` read the same
#: four, and the two spellings saying different things is what made SKILL.md
#: claim `--with` read a TOML file it could not.
_WITH_FORMATS = "YAML, JSON, JSONL or TOML"


def with_spec_option(kind: str) -> Any:
    """The `--with` option, declared once for all seven commands that take it.

    `--with` grew up one command at a time, and by #491 the seven call sites
    had three different help strings between them, named only YAML when the
    loader reads four serializations, said nothing about being deprecated on
    the two `maniphest` ones, and carried a file completer on two of the
    seven - so TAB offered a `.png` on the other five. None of that was a
    decision; it was seven copies drifting.

    So there are no longer seven copies. The spelling, the completer, the
    deprecation notice and the list of formats come from here, and the one
    sentence that genuinely differs between creating and searching comes
    from `_WITH_BEHAVIOUR` above.

    Parameters
    ----------
    kind : str
        "create" or "search", the kind of spec this command runs. It picks
        both the replacement spelling named in the help and the sentence
        about what the options beside `--with` do.

    Returns
    -------
    The `typer.Option` to assign to the command's `with_template` parameter.
    """
    from phabfive.cli.completers import complete_spec_file

    what = "create everything" if kind == "create" else "run every search"

    return typer.Option(
        None,
        "--with",
        help=(
            f"Deprecated: {what} a {kind} spec holds ({_WITH_FORMATS}). "
            f"`{COMMAND_OF_KIND[kind]} FILE` is the form that stays. "
            f"{_WITH_BEHAVIOUR[kind]}"
        ),
        autocompletion=complete_spec_file,
    )


#: The object types of the *other* kind, per kind, as `Spec.items` spells
#: them. What `load_of_kind` looks for before it retries a file the loader
#: could not place: a document holding nothing of the other kind has nothing
#: that could be reinterpreted, which is the only case where passing a kind
#: is safe.
_OTHER_KIND_OBJECTS = {
    "create": ("search",),
    "search": ("task", "project", "paste"),
}


def load_of_kind(source: str, wanted: str) -> "Spec":
    """Read a spec without telling the loader what it is.

    Inference is left to do its job, so ``spec.envelope.kind`` afterwards is
    what the *file* says rather than what the command hoped. :func:`dispatch_kind`
    is what then compares the two.

    One retry, and only one: a file the loader could not place at all - a
    search spec carrying nothing but ``title:`` and ``description:``, which
    is the shape ``--kind`` was added for - is read again with the command's
    own kind. That retry is accepted only when the document holds nothing of
    the other kind, so an ambiguous file carrying both ``tasks:`` and
    ``searches:`` is still refused with the loader's own message rather than
    silently read as half of itself.

    Parameters
    ----------
    source : str
        The path, as the caller typed it
    wanted : str
        "create" or "search", the kind the command runs

    Returns
    -------
    Spec

    Raises
    ------
    PhabfiveException
        The file does not exist, does not parse, or does not say what kind
        of spec it is and could not be read as this one either. The caller
        reports it; nothing here prints.
    """
    from phabfive.exceptions import PhabfiveException
    from phabfive.spec import load_spec

    try:
        return load_spec(source)
    except PhabfiveException as first:
        try:
            retried = load_spec(source, kind=wanted)
        except PhabfiveException:
            raise first from None

        if any(retried.items(other) for other in _OTHER_KIND_OBJECTS[wanted]):
            # It holds the other kind's items too, so the loader's "which is
            # it?" stands: reading it as `wanted` would drop or invent items.
            raise first from None

        log.debug("%s does not say what kind of spec it is; read as %s", source, wanted)

        return retried


def dispatch_kind(spec: Any, wanted: str, source: str) -> None:
    """Refuse a spec of the other kind, naming the command that does run it.

    Parameters
    ----------
    spec : Spec
        The loaded spec, whose ``envelope.kind`` is what the file says
    wanted : str
        "create" or "search", the kind this command runs
    source : str
        The path, as the caller typed it, so the sentence can be copied

    Raises
    ------
    typer.Exit
        Status 1 - the same status a spec that cannot be read leaves with,
        because in both cases nothing was run and nothing was checked.
    """
    found = spec.envelope.kind.value

    if found == wanted:
        return

    typer.echo(
        f"Error: {source} is a {found} spec. Run it with\n"
        f"  {COMMAND_OF_KIND[found]} {source}",
        err=True,
    )
    raise typer.Exit(EXIT_OFFLINE)


def warn_with_deprecated(replacement: str) -> None:
    """--with still works. Say once what replaces it, and get out of the way.

    A ``log.warning`` rather than a break, which is how this repository has
    deprecated before: ``phabfive/core.py`` says the same kind of thing about
    ``PHAB_URL`` in ``~/.config/phabfive.yaml``. ``--with`` is documented,
    shipped and in people's scripts, and carrying a two-line shim until the
    format promotes from ``v1alpha1`` to ``v1`` costs nothing.

    Parameters
    ----------
    replacement : str
        The new form, as a person would type it
    """
    log.warning("--with is deprecated; run `%s` instead", replacement)
