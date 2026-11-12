# -*- coding: utf-8 -*-
"""``phabfive export``: an object that exists, written as a spec.

The mirror of ``phabfive apply -f``. What a show command prints is for a
person and cannot be typed back in; what this prints is the same object in
the grammar a spec takes, so it can be edited and applied:

    phabfive export T123 > task.yaml
    $EDITOR task.yaml
    phabfive apply -f task.yaml

The data is `phabfive.export`'s. What is added here is where the monograms
come from - arguments, or the output of a search on standard input - and
where the document goes.
"""

import json
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional

import typer

from phabfive.exceptions import PhabfiveException

__all__ = ["ExportKind", "export_command"]

#: A monogram on its own, or as the last thing in a link to the object.
_MONOGRAM = re.compile(r"(?:^|/)([A-Za-z][0-9]+)$")

#: The formats a spec can be written in, which are the ones one is read in.
_FORMATS = ("yaml", "json", "jsonl")


class ExportKind(str, Enum):
    """What ``--as`` accepts."""

    edit = "edit"
    create = "create"


def _monogram(text: Any) -> str:
    """The monogram in `T123` or in a link to it.

    Raises
    ------
    PhabfiveException
        The text holds neither.
    """
    from phabfive.exceptions import PhabfiveInputException

    match = _MONOGRAM.search(str(text).strip())

    if match is None:
        raise PhabfiveInputException(f"{text!r} is not a monogram such as T123")

    return match.group(1).upper()


def _piped(text: str) -> List[str]:
    """The monograms on standard input, however they were written.

    Bare monograms separated by white space, or what a search prints in
    ``--format=yaml``, ``json`` or ``jsonl``: records carrying a ``Link``.
    The second is what makes ``phabfive maniphest search ... | phabfive
    export`` work, the same way it feeds ``phabfive edit``.
    """
    from ruamel.yaml import YAML

    from phabfive.exceptions import PhabfiveDataException

    words = text.split()

    if words and all(_MONOGRAM.search(word) for word in words):
        return [_monogram(word) for word in words]

    found: List[str] = []

    try:
        # JSON Lines first: one record per line with nothing between them is
        # not one YAML stream, and it is what `--format=jsonl` prints
        documents = [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError:
        documents = None

    try:
        if documents is None:
            documents = [
                document for document in YAML(typ="safe").load_all(text) if document
            ]
    except Exception as error:  # noqa: BLE001 - any parser failure is one answer
        raise PhabfiveDataException(
            f"Standard input is neither monograms nor search output: {error}"
        ) from None

    for document in documents:
        for entry in document if isinstance(document, list) else [document]:
            if not isinstance(entry, dict) or "Link" not in entry:
                raise PhabfiveDataException(
                    "Standard input holds a record with no Link to say what it "
                    "is. Pipe in monograms, or the output of a search in "
                    "--format=yaml, json or jsonl"
                )

            found.append(_monogram(entry["Link"]))

    return found


def export_command(
    ctx: typer.Context,
    monograms: Optional[List[str]] = typer.Argument(
        None,
        metavar="[MONOGRAM]...",
        help="What to export, e.g. T123 T124. Read from standard input when absent.",
    ),
    as_kind: ExportKind = typer.Option(
        ExportKind.edit,
        "--as",
        help=(
            "edit: a spec that changes these objects, applied unchanged it "
            "changes nothing. create: a spec that creates new objects like "
            "them."
        ),
    ),
    output: Optional[str] = typer.Option(
        None,
        "-o",
        "--output",
        metavar="FILE",
        help="Write the spec here instead of to standard output.",
    ),
) -> None:
    """Write objects that exist as a spec `phabfive apply -f` reads.

    Every value is written the way a spec takes it rather than the way a
    show command displays it: a status by its key, a project by its
    hashtag, a policy by its keyword.

    As an edit spec (the default) each object is named and stamped with when
    it was last modified. Applying the file unchanged changes nothing; edit
    it and apply it, and only the difference is sent. If somebody else has
    changed the object in between, the apply is refused rather than
    overwriting them.

    As a create spec the identity, the stamp and the links to other objects
    are left out, so applying it creates a new object like the old one.

    The output is YAML unless --format says json or jsonl. Tasks are
    exported today.

    \b
    Examples:
        phabfive export T123
        phabfive export T123 T124 -o tasks.yaml
        phabfive export T123 --as create | phabfive apply -f -
        phabfive maniphest search --tag=backend | phabfive export
        phabfive export T123 | phabfive apply -f - --dry-run
    """
    from phabfive.cli.apps import get_app
    from phabfive.constants import FORMAT_ALIASES

    asked = (ctx.obj or {}).get("format")
    output_format = FORMAT_ALIASES.get(asked, asked) if asked else "yaml"

    if output_format not in _FORMATS:
        typer.echo(
            f"Error: a spec is written as {', '.join(_FORMATS)}, not as "
            f"{output_format}.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        if monograms:
            wanted = [_monogram(one) for one in monograms]
        elif sys.stdin.isatty():
            typer.echo(
                "Error: nothing to export. Name an object, e.g. phabfive "
                "export T123, or pipe in a search.",
                err=True,
            )
            raise typer.Exit(1)
        else:
            wanted = _piped(sys.stdin.read())

            if not wanted:
                typer.echo("Error: standard input named nothing to export.", err=True)
                raise typer.Exit(1)

        # Imported here rather than at module import, like the spec engine
        # in `apply`: every other command would otherwise pay for it
        from phabfive.export import dump_spec, export_spec
        from phabfive.maniphest import Maniphest

        spec = export_spec(get_app(Maniphest), wanted, kind=as_kind.value)
        text = dump_spec(spec, output_format)
    except PhabfiveException as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from None

    if output is None or output == "-":
        sys.stdout.write(text)
        return

    try:
        Path(output).write_text(text, encoding="utf-8")
    except OSError as error:
        typer.echo(f"Error: could not write {output}: {error}", err=True)
        raise typer.Exit(1) from None
