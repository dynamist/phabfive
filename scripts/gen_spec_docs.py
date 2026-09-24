#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Write the generated tables of docs/phorge-spec.md from the declarations.

docs/phorge-spec.md defines the `phorge/v1alpha1` spec format. Most of it is
prose, but the parts a reader actually looks things up in - which keys each
object type takes, what each key's value is, which Conduit constraint a search
key is sent as, the problem-code vocabulary and the small grammars - are
tables, and a hand-written table drifts. docs/search-templates.md documented
`created-before` and `updated-before` while the loader refused them, for three
releases (#295).

So the tables are written from `phabfive.spec.registry`,
`phabfive.spec.references`, `phabfive.spec.problems` and friends into the
checked-in file, between literal markers::

    <!-- BEGIN GENERATED keys task/search -->
    ...
    <!-- END GENERATED keys task/search -->

The file on disk stays complete: the page has to be readable on GitHub, in a
plain editor and to `grep`, without a build step, because a build-time include
would put the definition of the format somewhere no reader of the repository
can see it. `tests/test_spec_docs.py` asserts the checked-in file equals what
this script would write, and fails telling you to run it.

    python3 scripts/gen_spec_docs.py            # rewrite the page in place
    python3 scripts/gen_spec_docs.py --check    # exit 1 if it is stale

Only the blocks between markers are touched. Everything else in the page is
written by hand.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phabfive.spec.envelope import (  # noqa: E402
    DEFAULT_SEARCH_TYPE,
    ENVELOPE_KEYS,
    METADATA_KEYS,
    SPEC_VERSION,
    SUPPORTED_SPEC_VERSIONS,
    Kind,
)
from phabfive.spec.problems import CODES  # noqa: E402
from phabfive.spec.references import (  # noqa: E402
    CREATE_OBJECT_KEYS,
    CREATION_KEYS,
    LOCAL_ID_KEY,
    LOCAL_ID_PATTERN,
    NESTED_KEY,
    REFERENCE_FIELDS,
    UNCREATABLE_OBJECT_KEYS,
)
from phabfive.spec.registry import (  # noqa: E402
    OBJECT_TYPES,
    Field,
    FieldKind,
    constraint_for,
    fields_for,
    spec_keys,
)
from phabfive.spec.schema import SEARCH_ITEM_KEYS  # noqa: E402

#: The page the blocks are written into.
PAGE = _REPO / "docs" / "phorge-spec.md"

#: The (object type, verb) pairs that get a key table, in reading order:
#: create before search, and within each the order a document writes them.
PAIRS: tuple[tuple[str, str], ...] = tuple(
    (object_type, verb)
    for verb in ("create", "search")
    for object_type in ("task", "project", "paste", "passphrase")
)

#: What each problem code means, and which layer reports it. A code is a
#: promise to whoever branches on it, so it is documented here rather than in
#: the page, where nothing would notice a new one. `blocks()` refuses to run
#: when this disagrees with `phabfive.spec.problems.CODES` in either
#: direction, which is what stops the table going stale the day a check is
#: added.
CODE_MEANINGS: dict[str, tuple[str, str, str]] = {
    # code: (layer, severity, meaning)
    "unknown-spec-version": (
        "offline",
        "error",
        "`spec:` names a revision of the format the reader does not implement.",
    ),
    "unknown-key": (
        "offline",
        "error, warning under `metadata:`",
        "A key that object type and verb does not define.",
    ),
    "deprecated-key": (
        "offline",
        "warning",
        "The key still works; the reason names its replacement.",
    ),
    "wrong-type": (
        "offline",
        "error",
        "The value is a number, list or mapping where the key takes text, "
        "or the other way round.",
    ),
    "missing-required": (
        "offline",
        "error",
        "An item leaves out a key it cannot be created without.",
    ),
    "unknown-value": (
        "offline",
        "error",
        "The value is outside a statically known set, such as an order or a "
        "project colour.",
    ),
    "bad-time": ("offline", "error", "Not `<number>[h|d|w|m|y]`."),
    "bad-pattern": (
        "offline",
        "error",
        "Not the transition grammar: an unknown condition type, keyword or direction.",
    ),
    "bad-policy": (
        "offline",
        "error",
        "Not a policy keyword, `#project`, `@user` or PHID.",
    ),
    "bad-monogram": (
        "offline and online",
        "error",
        "Not a monogram at all, or a monogram of an application this key does "
        "not accept.",
    ),
    "undefined-variable": (
        "offline",
        "error",
        "A `{{ name }}` nothing declares, overrides or defaults.",
    ),
    "missing-variable": (
        "offline",
        "error",
        "A declared variable with no value, no default and no override.",
    ),
    "circular-variable": ("offline", "error", "Variables that define each other."),
    "duplicate-local-id": (
        "offline",
        "error",
        "Two items declare the same `id:`.",
    ),
    "unknown-local-id": (
        "offline",
        "error",
        "A `$ref` naming a local id the document never declares, or one of the "
        "wrong object type for the key.",
    ),
    "local-id-cycle": (
        "offline",
        "error",
        "Local references that depend on each other, so no order creates them.",
    ),
    "bad-local-id": (
        "offline",
        "error",
        f"An `id:` outside `{LOCAL_ID_PATTERN}`.",
    ),
    "not-creatable": (
        "offline",
        "error",
        "A section naming an object type no endpoint creates.",
    ),
    "unknown-user": ("online", "error", "No such user on the instance."),
    "unknown-commit": ("online", "error", "No such commit on the instance."),
    "ambiguous-commit": (
        "online",
        "error",
        "A bare hash more than one repository answers to; use rCALLSIGNhash or R1:hash.",
    ),
    "unknown-project": ("online", "error", "No such project on the instance."),
    "ambiguous-project": (
        "online",
        "error",
        "A name several projects answer to; use the hashtag or the PHID.",
    ),
    "unknown-space": ("online", "error", "No such Space on the instance."),
    "unknown-icon": (
        "online",
        "warning",
        "An icon no project carries. The icon set is instance configuration "
        "no method reports, so this is never an error.",
    ),
    "unknown-status": (
        "online",
        "error",
        "A status key this instance does not define.",
    ),
    "unknown-priority": (
        "online",
        "error",
        "A priority name this instance does not define.",
    ),
    "unknown-column": (
        "online",
        "error",
        "A column that is on none of the boards the item is tagged into.",
    ),
    "hashtag-taken": (
        "online",
        "error",
        "A project hashtag something already answers to.",
    ),
    "unknown-reference": (
        "online",
        "error",
        "A reference that resolved to nothing and no more specific code fits.",
    ),
    "unreadable": (
        "neither",
        "error",
        "The document could not be parsed, so there was nothing to validate.",
    ),
    "unreachable": (
        "neither",
        "error",
        "The instance could not be asked, so the online layer never ran.",
    ),
}

#: How a `FieldKind` is named to a reader of the page. The enum's own values
#: are `instance-enum` and `text`; these say what the *value* is.
KIND_NAMES: dict[FieldKind, str] = {
    FieldKind.TEXT: "text",
    FieldKind.INT: "integer",
    FieldKind.BOOL: "boolean",
    FieldKind.TIME: "time",
    FieldKind.ENUM: "enum",
    FieldKind.ORDER: "order",
    FieldKind.PATTERN: "pattern",
    FieldKind.USER: "user reference",
    FieldKind.PROJECT: "project reference",
    FieldKind.SPACE: "Space reference",
    FieldKind.POLICY: "policy",
    FieldKind.MONOGRAM: "monogram",
    FieldKind.COMMIT: "commit reference",
    FieldKind.INSTANCE_ENUM: "instance enum",
}

_DASH = "&mdash;"


def _cell(text: object) -> str:
    """One table cell, with the characters a Markdown table cannot hold gone."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _table(header: Iterable[str], rows: Iterable[Iterable[object]]) -> list[str]:
    """A Markdown table, or a sentence when there is nothing to put in one."""
    columns = list(header)
    body = [[_cell(cell) for cell in row] for row in rows]

    if not body:
        return []

    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in body]

    return lines


def _value(field: Field) -> str:
    """What one key's value is, in a few words."""
    name = KIND_NAMES[field.kind]

    if field.kind is FieldKind.ENUM and field.choices:
        return f"{name}: {', '.join(f'`{choice}`' for choice in field.choices)}"

    if field.kind is FieldKind.MONOGRAM and field.monograms:
        letters = ", ".join(f"`{letter}123`" for letter in field.monograms)
        return f"{name}: {letters}"

    return name


def _default(field: Field) -> str:
    """The value a reader applies when the key is absent."""
    if field.default is None:
        return _DASH

    if isinstance(field.default, bool):
        return f"`{str(field.default).lower()}`"

    return f"`{field.default}`"


def _meaning(field: Field) -> str:
    """One key's help, with its deprecation said first when it has one."""
    if field.deprecated:
        return f"**Deprecated**, write `{field.deprecated}` instead. {field.help}"

    return field.help


def _keys_block(object_type: str, verb: str) -> list[str]:
    """One key table, for one object type and one verb."""
    fields = fields_for(object_type, verb)

    if not fields:
        refused = dict(UNCREATABLE_OBJECT_KEYS)
        section = f"{object_type}s"

        if verb == "create" and section in refused:
            return [
                f"No `{section}:` section exists. {refused[section][0].upper()}"
                f"{refused[section][1:]}"
            ]

        return [f"No key is defined for {object_type} {verb} yet."]

    if verb == "search":
        header = ("Key", "Value", "Repeatable", "Default", "Sent as", "Meaning")
        rows = [
            (
                f"`{field.name}`",
                _value(field),
                "yes" if field.multiple else _DASH,
                _default(field),
                (
                    f"`{constraint_for(field, object_type)}`"
                    if constraint_for(field, object_type)
                    else _DASH
                ),
                _meaning(field),
            )
            for field in fields
        ]
    else:
        header = ("Key", "Value", "Repeatable", "Meaning")
        rows = [
            (
                f"`{field.name}`",
                _value(field),
                "yes" if field.multiple else _DASH,
                _meaning(field),
            )
            for field in fields
        ]

    return _table(header, rows)


def _structural_block() -> list[str]:
    """The create keys that are structure rather than a value.

    They are not `Field`s and never will be: `id:` is a name, `tasks:` is a
    list of child items, and the three linking keys hold references. A table
    generated from the registry alone would document an incomplete format, so
    this one reads `phabfive.spec.references` instead.
    """
    rows: list[tuple[str, str, str, str]] = [
        (
            f"`{LOCAL_ID_KEY}`",
            "any item",
            f"`{LOCAL_ID_PATTERN}`",
            "Names this item inside this document, so `$id` elsewhere in it "
            "means the object this item creates.",
        ),
        (
            f"`{NESTED_KEY}`",
            "task items",
            "list of task items",
            "Child tasks. Each child is created with this item as a parent.",
        ),
    ]

    described = {
        "parent": "One task this item hangs off.",
        "parents": "Tasks this item hangs off.",
        "subtasks": "Tasks that hang off this item.",
    }

    for object_type, reference_fields in sorted(REFERENCE_FIELDS.items()):
        if object_type not in dict(CREATE_OBJECT_KEYS).values():
            continue

        declared = spec_keys(object_type, "create")

        for reference_field in reference_fields:
            if reference_field.name in declared:
                continue

            monograms = (
                ", ".join(f"`{letter}123`" for letter in reference_field.monograms)
                or "reference"
            )
            shape = "list of " + monograms if reference_field.multiple else monograms

            rows.append(
                (
                    f"`{reference_field.name}`",
                    f"{object_type} items",
                    shape,
                    described.get(reference_field.name, ""),
                )
            )

    return _table(("Key", "Where", "Value", "Meaning"), rows)


def _envelope_block() -> list[str]:
    """The envelope keys, and what `metadata:` holds."""
    described = {
        "spec": (
            f"The revision of the format, `{SPEC_VERSION}`. Absent means "
            "the reader's own revision is assumed."
        ),
        "kind": (
            "`"
            + "` or `".join(sorted(kind.value for kind in Kind))
            + "`. Absent means inferred from the body keys."
        ),
        "metadata": "What the *file* is, for a person reading it. Nothing acts on it.",
    }

    rows = [(f"`{key}`", "optional", described[key]) for key in sorted(ENVELOPE_KEYS)]
    lines = _table(("Key", "Required", "Meaning"), rows)

    metadata_described = {
        "name": "A short name for the file. Not a Phorge object's name.",
        "description": "What the file is for, in a sentence or two.",
        "version": "The file's own revision, as text. `version: 2.1` is a float.",
        "author": "Who maintains the file.",
    }

    lines += ["", "`metadata:` holds:", ""]
    lines += _table(
        ("Key", "Value", "Meaning"),
        [(f"`{key}`", "text", metadata_described[key]) for key in METADATA_KEYS],
    )
    lines += [
        "",
        "Every key above is optional, and a `metadata:` key outside this "
        "table is reported as a warning rather than dropped.",
        "",
        "Accepted `spec:` values: "
        + ", ".join(f"`{value}`" for value in sorted(SUPPORTED_SPEC_VERSIONS))
        + ".",
    ]

    return lines


def _kinds_block() -> list[str]:
    """The two kinds, and the object types each admits."""
    creatable = ", ".join(f"`{key}:`" for key, _ in CREATE_OBJECT_KEYS)
    refused = "; ".join(
        f"`{key}:` &mdash; {why}" for key, why in UNCREATABLE_OBJECT_KEYS
    )
    searchable = ", ".join(f"`{name}`" for name in sorted(OBJECT_TYPES))
    creation = "; ".join(
        f"a {object_type} by its " + " and ".join(f"`{key}:`" for key in keys)
        for object_type, keys in sorted(CREATION_KEYS.items())
    )

    rows = [
        (
            "`create`",
            creatable,
            f"One section per object type, each a list of items. An item creates "
            f"something when it names it: {creation}.",
        ),
        (
            "`search`",
            "`searches:`",
            f"One list of items, each naming what it searches with its own "
            f"`type:`: {searchable}. `type:` defaults to `{DEFAULT_SEARCH_TYPE}`.",
        ),
    ]

    lines = _table(("`kind:`", "Body keys", "What it holds"), rows)
    lines += ["", f"Refused by name rather than ignored: {refused}"]

    return lines


def _search_item_block() -> list[str]:
    """The keys of one `searches:` item."""
    described = {
        "type": (
            "What this item searches: "
            + ", ".join(f"`{name}`" for name in sorted(OBJECT_TYPES))
            + f". Defaults to `{DEFAULT_SEARCH_TYPE}`."
        ),
        "title": "A banner for *this search's* results. Not the file's name.",
        "description": "What this search is for, shown with the banner.",
        "search": "The filters, whose keys are this `type:`'s search keys.",
    }

    return _table(
        ("Key", "Meaning"),
        [(f"`{key}`", described[key]) for key in SEARCH_ITEM_KEYS],
    )


def _codes_block() -> list[str]:
    """The problem vocabulary, in the order the codes are declared."""
    declared = set(CODES)
    documented = set(CODE_MEANINGS)

    if declared != documented:
        missing = ", ".join(sorted(declared - documented)) or "none"
        extra = ", ".join(sorted(documented - declared)) or "none"
        raise SystemExit(
            "CODE_MEANINGS disagrees with phabfive.spec.problems.CODES.\n"
            f"  declared but not documented: {missing}\n"
            f"  documented but not declared: {extra}\n"
            "Add the row in the change that adds the check."
        )

    rows = [
        (f"`{code}`", *CODE_MEANINGS[code][:2], CODE_MEANINGS[code][2])
        for code in CODES
    ]

    return _table(("Code", "Layer", "Severity", "Means"), rows)


def _grammar_block() -> list[str]:
    """The small grammars a value may be written in.

    Read out of the modules that enforce them, because a restated grammar is
    the other half of the drift the generated key tables close.
    """
    from phabfive.constants import (
        MANIPHEST_ORDER_CHOICES,
        MONOGRAMS,
        POLICY_KEYWORDS,
    )
    from phabfive.spec.times import TIME_UNITS
    from phabfive.transitions import (
        VALID_COLUMN_CONDITION_TYPES,
        VALID_COLUMN_KEYWORDS,
        VALID_PRIORITY_CONDITION_TYPES,
        VALID_PRIORITY_KEYWORDS,
        VALID_STATUS_CONDITION_TYPES,
        VALID_STATUS_KEYWORDS,
    )

    units = ", ".join(f"`{unit}`" for unit in TIME_UNITS)
    keywords = ", ".join(f"`{word}`" for word in POLICY_KEYWORDS)
    orders = ", ".join(f"`{choice}`" for choice in MANIPHEST_ORDER_CHOICES)
    monograms = ", ".join(
        f"`{pattern[0]}123` ({application})"
        for application, pattern in sorted(MONOGRAMS.items())
    )

    rows = [
        (
            "time",
            f"`<number>[<unit>]`, units {units}, days when the unit is omitted",
        ),
        ("policy", f"{keywords}, or `#project`, `@user`, `PHID-…`"),
        ("order", orders),
        ("monogram", monograms),
        (
            "pattern (`column`)",
            ", ".join(f"`{name}:`" for name in VALID_COLUMN_CONDITION_TYPES)
            + ", the keywords "
            + ", ".join(f"`{word}`" for word in VALID_COLUMN_KEYWORDS),
        ),
        (
            "pattern (`status`)",
            ", ".join(f"`{name}:`" for name in VALID_STATUS_CONDITION_TYPES)
            + ", the keywords "
            + ", ".join(f"`{word}`" for word in VALID_STATUS_KEYWORDS),
        ),
        (
            "pattern (`priority`)",
            ", ".join(f"`{name}:`" for name in VALID_PRIORITY_CONDITION_TYPES)
            + ", the keywords "
            + ", ".join(f"`{word}`" for word in VALID_PRIORITY_KEYWORDS),
        ),
        ("local id", f"`${LOCAL_ID_PATTERN}`"),
    ]

    return _table(("Value", "Written as"), rows)


def blocks() -> dict[str, list[str]]:
    """Every generated block, by the name its markers carry."""
    generated: dict[str, list[str]] = {
        "envelope": _envelope_block(),
        "kinds": _kinds_block(),
        "search-item": _search_item_block(),
        "create-structural": _structural_block(),
        "codes": _codes_block(),
        "grammar": _grammar_block(),
    }

    for object_type, verb in PAIRS:
        generated[f"keys {object_type}/{verb}"] = _keys_block(object_type, verb)

    return generated


def _marker(name: str, edge: str) -> str:
    return f"<!-- {edge} GENERATED {name} -->"


def render(text: str, generated: Optional[dict[str, list[str]]] = None) -> str:
    """The page with every generated block replaced by what it should hold.

    Raises
    ------
    SystemExit
        A block with no markers in the page, or a marker naming no block.
        Both mean the page and this script disagree about what is generated,
        which is exactly the state the markers exist to make impossible.
    """
    generated = blocks() if generated is None else generated

    found = set(re.findall(r"<!-- BEGIN GENERATED ([^>]+?) -->", text))
    expected = set(generated)

    if found != expected:
        missing = ", ".join(sorted(expected - found)) or "none"
        extra = ", ".join(sorted(found - expected)) or "none"
        raise SystemExit(
            f"{PAGE.name} and {Path(__file__).name} disagree about the blocks.\n"
            f"  generated but not in the page: {missing}\n"
            f"  in the page but not generated: {extra}"
        )

    for name, lines in generated.items():
        begin = re.escape(_marker(name, "BEGIN"))
        end = re.escape(_marker(name, "END"))
        body = "\n".join(lines)
        replacement = f"{_marker(name, 'BEGIN')}\n\n{body}\n\n{_marker(name, 'END')}"
        text, count = re.subn(
            rf"{begin}.*?{end}",
            lambda _match, replacement=replacement: replacement,
            text,
            flags=re.DOTALL,
        )

        if count != 1:
            raise SystemExit(f"{PAGE.name}: expected one {name!r} block, found {count}")

    return text


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 when the page is stale",
    )
    parser.add_argument(
        "page",
        nargs="?",
        type=Path,
        default=PAGE,
        help=f"the page to write (default: {PAGE.relative_to(_REPO)})",
    )
    arguments = parser.parse_args(argv)

    current = arguments.page.read_text(encoding="utf-8")
    wanted = render(current)

    if current == wanted:
        return 0

    if arguments.check:
        print(
            f"{arguments.page} is stale; run python3 scripts/gen_spec_docs.py",
            file=sys.stderr,
        )
        return 1

    arguments.page.write_text(wanted, encoding="utf-8")
    print(f"wrote {arguments.page}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
