# -*- coding: utf-8 -*-
"""Writing a spec from objects that exist.

`phabfive show` prints an object for a person: `Status: Open`, `Visible To:
All Users`. None of that can be typed back in. An export is the same object
in the grammar a spec takes - `status: open`, `visible-to: users` - as a
document `phabfive apply -f` reads:

    from phabfive import Maniphest
    from phabfive.export import dump_spec, export_spec

    app = Maniphest(url=URL, token=TOKEN)
    spec = export_spec(app, ["T123", "T124"])       # kind: edit
    print(dump_spec(spec))

Two kinds can be asked for:

- ``edit`` (the default) names each object and stamps it with when it was
  last modified. Applied unchanged it changes nothing; edit the file and
  apply it, and the difference is sent - see `phabfive.edit.spec`.
- ``create`` describes a *new* object like the old one: the identity, the
  stamp and the relations are left out, because a copy is not the same node
  in the graph.

Either way the document says ``interpolate: false``. Its text is whatever
the object held, and a description that quotes a template must not be read
as one.

Nothing here prints, prompts or writes a file; the command is `phabfive
export`.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from phabfive.exceptions import PhabfiveInputException
from phabfive.export.tasks import export_tasks
from phabfive.spec.envelope import SPEC_VERSION, Kind, Spec, as_kind

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    pass

__all__ = [
    "EXPORTED_TYPES",
    "EXPORT_KINDS",
    "dump_spec",
    "export_spec",
]

#: The object types an export writes, by the letter of their monogram, with
#: the body key each is written under. A type joins when its exporter and
#: the edit planner that reads it back both exist.
EXPORTED_TYPES: Mapping[str, tuple[str, str]] = {"T": ("task", "tasks")}

#: The kinds of document an export writes.
EXPORT_KINDS = (Kind.EDIT, Kind.CREATE)

_EXPORTERS = {"task": export_tasks}


def _split(monograms: Iterable[str]) -> dict[str, list[str]]:
    """The ids asked for, per object type, in the order given and once each."""
    wanted: dict[str, list[str]] = {}

    for monogram in monograms:
        text = str(monogram).strip()
        letter, number = text[:1].upper(), text[1:]
        named = EXPORTED_TYPES.get(letter)

        if named is None or not number.isdigit():
            known = ", ".join(f"{letter}123" for letter in sorted(EXPORTED_TYPES))
            raise PhabfiveInputException(
                f"{text!r} is not something an export writes. It takes: {known}"
            )

        ids = wanted.setdefault(named[0], [])

        if number not in ids:
            ids.append(number)

    return wanted


def export_spec(app: Any, monograms: Sequence[str], *, kind: Any = Kind.EDIT) -> Spec:
    """A spec describing the objects `monograms` name, as they are now.

    Parameters
    ----------
    app : Phabfive
        An already-constructed app; a sibling sharing its client is built
        for each object type that needs one.
    monograms : sequence of str
        What to export, e.g. ``["T123", "T124"]``. A repeat is exported
        once.
    kind : Kind or str, optional
        "edit" (the default) or "create".

    Returns
    -------
    Spec
        One document holding every object, in the order asked for.

    Raises
    ------
    PhabfiveInputException
        A monogram of a type an export does not write, or a kind that is
        neither "edit" nor "create".
    PhabfiveNotFoundException
        An object that does not exist, naming every one that does not.
    """
    from phabfive.create.dispatch import app_for

    resolved = as_kind(kind, where="kind argument")

    if resolved not in EXPORT_KINDS:
        raise PhabfiveInputException(
            f"An export is written as "
            f"{' or '.join(one.value for one in EXPORT_KINDS)}, not as "
            f"{resolved.value}"
        )

    document: dict[str, Any] = {
        "spec": SPEC_VERSION,
        "kind": resolved.value,
        "interpolate": False,
    }

    for object_type, ids in _split(monograms).items():
        section = next(
            key for name, key in EXPORTED_TYPES.values() if name == object_type
        )
        document[section] = _EXPORTERS[object_type](
            app_for(object_type, app), ids, kind=resolved
        )

    return Spec.from_data(document)


def _literal(value: Any) -> Any:
    """Multi-line text as a YAML block, where a block can hold it exactly.

    A block scalar reads far better than a quoted string full of `\\n`, and
    it is what somebody editing a description wants in front of them. It
    cannot carry everything, though: a carriage return, a line ending in
    white space or a leading space on the first line are normalized or lost
    in one. Those stay quoted. `dump_spec` checks the result parses back to
    the same spec either way, so this is about readability, never fidelity.
    """
    from ruamel.yaml.scalarstring import LiteralScalarString

    if isinstance(value, Mapping):
        return {key: _literal(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_literal(item) for item in value]

    if not isinstance(value, str) or "\n" not in value:
        return value

    lines = value.split("\n")

    if (
        "\r" in value
        or "\t" in value
        or lines[0][:1] == " "
        or any(line != line.rstrip() for line in lines)
        or not value.strip("\n")
    ):
        return value

    return LiteralScalarString(value)


def _yaml(data: Mapping[str, Any]) -> str:
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    # Never fold: a long title wrapped over two lines is still one string to
    # YAML and two lines to whoever is editing it
    yaml.width = 1 << 20
    yaml.indent(mapping=2, sequence=4, offset=2)

    buffer = io.StringIO()
    yaml.dump(_literal(dict(data)), buffer)

    return buffer.getvalue()


def dump_spec(spec: Spec, format: str = "yaml") -> str:
    """The spec as text that parses back to exactly this spec.

    Parameters
    ----------
    spec : Spec
    format : str, optional
        "yaml" (the default), "json" or "jsonl".

    Returns
    -------
    str
        The document, ending in a newline.

    Notes
    -----
    **The text is checked before it is returned.** It is parsed back and
    compared with `spec`, and YAML that does not survive the trip - there is
    always some string a prettier style cannot hold - is replaced by the
    JSON form, which is YAML too and holds anything. An export that applied
    as a change nobody made would be worse than one that reads less well.
    """
    from phabfive.spec.loader import parse_spec

    data = spec.to_data()

    if format == "json":
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    if format == "jsonl":
        return json.dumps(data, ensure_ascii=False) + "\n"

    if format != "yaml":
        raise PhabfiveInputException(
            f"A spec is written as yaml, json or jsonl, not as {format!r}"
        )

    text = _yaml(data)

    try:
        same = parse_spec(text, format="yaml") == spec
    except Exception:  # noqa: BLE001 - any failure means "not the same"
        same = False

    if not same:
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    return text
