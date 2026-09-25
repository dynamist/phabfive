# -*- coding: utf-8 -*-
"""A JSON Schema document, generated from the field registry.

`build_schema` produces a real JSON Schema 2020-12 document describing one
kind of spec. It is generated rather than written, from
`phabfive.spec.registry` and `phabfive.spec.envelope`, so a field declared
once is a key the loader accepts, a flag the command carries **and** a
property an editor completes.

Two things this module is deliberately not:

- **It is not the validator.** `phabfive.spec.validate` walks a spec against
  the registry directly and never reads this document, so no JSON Schema
  library is a runtime dependency of phabfive. The value of the document is
  that other tools - an editor, a pre-commit hook in another language, a CI
  job - can check a spec without phabfive at all. The suite uses
  ``jsonschema`` as a test oracle for exactly that reason.
- **It is not where an instance's vocabulary lives.** Priorities, statuses,
  project icons, spaces and policy *names* are defined by the Phorge
  instance, and `phabfive.maniphest` answers a failed status fetch with
  invented defaults. Baking either into a generated schema would let it
  bless a value the server never named, so those keys are typed and
  shape-checked here and resolved by the online pass. What is in the schema
  is only what is genuinely static: the spec version, the kinds, the order
  grammar, the transition grammar, the *shape* of a policy - and the project
  colours, which belong on this side of the line: their keys are fixed in
  Phorge's source, so `projects.colors` relabels a colour but cannot add
  one, and a `FieldKind.ENUM` publishes the ten.

Library code: it prints nothing, reads no configuration and opens no socket.
`phabfive.constants` and `phabfive.transitions` are imported inside the
functions that need them. Not for a cycle - `phabfive.constants` is a leaf
of plain literals - but because generating a schema is not something every
importer of this file does, and `functools.lru_cache` makes the deferred
import cost one dict lookup.
"""

from __future__ import annotations

import functools
import re
from typing import Any, Optional

from phabfive.spec.envelope import (
    DEFAULT_SEARCH_TYPE,
    LEGACY_SEARCH_KEYS,
    METADATA_KEYS,
    SUPPORTED_SPEC_VERSIONS,
    VARIABLES_KEY,
    Kind,
    KindLike,
    as_kind,
)
from phabfive.spec.references import (
    CREATE_OBJECT_KEYS,
    LOCAL_ID_KEY,
    LOCAL_ID_PATTERN,
    NESTED_KEY,
)
from phabfive.spec.registry import (
    DECLARED_COMPLETE,
    OBJECT_TYPES,
    Field,
    FieldKind,
    fields_for,
)

__all__ = [
    "JSON_SCHEMA_DIALECT",
    "LEGACY_SEARCH_KEYS",
    "SEARCH_ITEM_KEYS",
    "TIME_PATTERN",
    "build_schema",
    "enum_list_pattern",
    "monogram_grammar_alternatives",
    "monogram_list_pattern",
    "monogram_pattern",
    "policy_pattern",
    "property_schema",
    "time_pattern",
    "transition_pattern",
]

#: The dialect every generated document declares.
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

#: The keys one `searches:` item carries, beside the `search:` mapping whose
#: keys are the registry's. Derived from the legacy flat spelling rather than
#: restated: `phabfive.spec.envelope` folds `{title, description, search}`
#: into an item and adds `type`, so the two lists are one fact and a third
#: banner key would have to be added in only one place.
SEARCH_ITEM_KEYS = ("type",) + LEGACY_SEARCH_KEYS

#: What `1h`, `7d`, `2w` or a bare `14` looks like: a non-negative number
#: with an optional unit, and whatever whitespace around it
#: `phabfive.spec.times.parse_time_with_unit` strips before reading it.
#:
#: This is the *documented* grammar, and it is deliberately narrower than
#: that parser, whose backward-compatible "a bare number is days" branch
#: hands the text to `float()` and therefore also reads `1e3`, `nan` and
#: `1_0`. Those are accidents, not part of the format, so nothing publishes
#: them - and nothing rejects them either, because the offline pass calls
#: the parser rather than matching this pattern. The published document is
#: thus never *wider* than phabfive, which is the direction that matters for
#: an editor or a CI job that has only the schema.
TIME_PATTERN = r"^[ \t]*[0-9]+(?:\.[0-9]+)?[ \t]*(?:[hdwmyHDWMY])?[ \t]*$"

# The value half of one transition condition. `:` separates the condition
# type from the value and the value from the direction, `+` ANDs conditions
# and `,` ORs groups, so none of the three can appear inside a value. The
# parser strips the value and refuses an empty one, so at least one
# character of it has to be something other than whitespace.
_PATTERN_VALUE = r"[^,+:]*[^\s,+:][^,+:]*"

# What separates two conditions. `phabfive.transitions.split_pattern_groups`
# splits on "," then on "+", strips each part and drops the empty ones, so
# "in:A,", "+in:A", "in:A++in:B" and "  in:A  " are all accepted and a
# published pattern refusing them would be stricter than the parser phabfive
# actually runs.
_PATTERN_SEPARATOR = r"[\s,+]+"

# The one condition type a direction may follow.
# `phabfive.transitions.parse_direction` refuses one on any other, so the
# published pattern says so too rather than accepting `in:Done:forward`.
_DIRECTION_TYPE = "from"

# Which transition grammar each PATTERN field speaks. A field not listed gets
# the union of all three, which is the honest answer for a grammar this
# module has not been told about.
_PATTERN_ENTITIES = {
    "column": "column",
    "status": "status",
    "priority": "priority",
}


def time_pattern() -> str:
    """The ECMA-262 pattern a `FieldKind.TIME` string must match."""
    return TIME_PATTERN


@functools.lru_cache(maxsize=None)
def _transition_vocabulary(
    entity: Optional[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """The condition types, keywords and directions of one transition grammar.

    Read off `phabfive.transitions` rather than restated, so the generated
    pattern cannot drift from the parser the offline pass actually runs.
    """
    from phabfive import transitions

    vocabularies = {
        "column": (
            transitions.VALID_COLUMN_CONDITION_TYPES,
            transitions.VALID_COLUMN_KEYWORDS,
            transitions.VALID_COLUMN_DIRECTIONS,
        ),
        "status": (
            transitions.VALID_STATUS_CONDITION_TYPES,
            transitions.VALID_STATUS_KEYWORDS,
            transitions.VALID_STATUS_DIRECTIONS,
        ),
        "priority": (
            transitions.VALID_PRIORITY_CONDITION_TYPES,
            transitions.VALID_PRIORITY_KEYWORDS,
            transitions.VALID_PRIORITY_DIRECTIONS,
        ),
    }

    if entity in vocabularies:
        chosen = [vocabularies[entity]]
    else:
        chosen = list(vocabularies.values())

    types: list[str] = []
    keywords: list[str] = []
    directions: list[str] = []

    for one_types, one_keywords, one_directions in chosen:
        types += list(one_types)
        keywords += list(one_keywords)
        directions += list(one_directions)

    def unique(values: list[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))

    return unique(types), unique(keywords), unique(directions)


def transition_pattern(entity: Optional[str] = None) -> str:
    """The ECMA-262 pattern a transition filter must match.

    The grammar, said once::

        condition := ["not:"] ( keyword | type ":" value [ ":" direction ] )
        group     := condition ("+" condition)*
        filter    := group ("," group)*

    `+` ANDs the conditions of one group, `,` ORs the groups. The vocabulary
    comes from `phabfive.transitions`, so ``from``/``to``/``in``/``been``/
    ``never`` and each entity's keywords are whatever that module says they
    are today.

    Parameters
    ----------
    entity : str, optional
        ``"column"``, ``"status"`` or ``"priority"``. None, or a name this
        module has not been told about, gets the union of all three.
    """
    types, keywords, directions = _transition_vocabulary(entity)

    def alternatives(values: tuple[str, ...]) -> str:
        return "|".join(re.escape(value) for value in values)

    keyword_alt = alternatives(keywords)
    plain = tuple(one for one in types if one != _DIRECTION_TYPE)

    # The parser strips around "not:", around the condition type and around
    # the direction, so each of those may carry whitespace. A direction is
    # spelled only after `from:` - `phabfive.transitions.parse_direction`
    # refuses one on any other condition type - so the two branches differ.
    branches = [f"(?:{keyword_alt})"]

    if _DIRECTION_TYPE in types:
        branches.append(
            f"{re.escape(_DIRECTION_TYPE)}\\s*:{_PATTERN_VALUE}"
            f"(?:\\s*:\\s*(?:{alternatives(directions)})\\s*)?"
        )

    if plain:
        branches.append(f"(?:{alternatives(plain)})\\s*:{_PATTERN_VALUE}")

    condition = "(?:not:\\s*)?(?:" + "|".join(branches) + ")"
    # A string of nothing but separators still has to fail, exactly as the
    # parser's "No valid patterns found" does, which is why a condition is
    # required between them.
    edge = f"(?:{_PATTERN_SEPARATOR})?"

    return f"^{edge}{condition}(?:{_PATTERN_SEPARATOR}{condition})*{edge}$"


@functools.lru_cache(maxsize=None)
def policy_pattern() -> str:
    """The ECMA-262 pattern a policy value's *shape* must match.

    Shape only, which is the whole point: whether ``#platform`` is a project
    that exists is the online pass's question. The keyword list comes from
    `phabfive.constants`, imported here for the reason the module docstring
    gives.

    ``$local-id`` is one of the shapes. A create spec may make a task visible
    to a project the same document creates, which is what
    ``specs/create/platform-bootstrap.yaml`` does, and the hand-rolled walk
    accepts it in any policy field - so a schema that did not would call a
    document invalid that every other reader plans cleanly.
    """
    from phabfive.constants import POLICY_KEYWORDS

    keywords = "|".join(re.escape(keyword) for keyword in POLICY_KEYWORDS)

    return f"^(?:(?:{keywords})|PHID-.+|#.+|@.+|\\${LOCAL_ID_PATTERN})$"


@functools.lru_cache(maxsize=None)
def monogram_pattern() -> str:
    """The ECMA-262 pattern one monogram must match, any application."""
    from phabfive.constants import MONOGRAMS

    return "^(?:" + "|".join(sorted(MONOGRAMS.values())) + ")$"


@functools.lru_cache(maxsize=None)
def monogram_list_pattern(prefixes: tuple[str, ...] = ()) -> str:
    """The pattern a comma-separated string of monograms must match.

    ``--include`` and ``include:`` take ``"T1,T2"`` as readily as
    ``["T1", "T2"]``, so the scalar spelling is a list too.

    Parameters
    ----------
    prefixes : tuple of str, optional
        Restrict to these application letters, e.g. ``("T",)`` for a key
        that takes task ids. Empty accepts any application's monogram, which
        is what a key that has not said carries.
    """
    one = "(?:" + "|".join(sorted(monogram_grammar_alternatives(prefixes))) + ")"

    return f"^[ \\t]*{one}(?:[ \\t]*,[ \\t]*{one})*[ \\t]*$"


@functools.lru_cache(maxsize=None)
def enum_list_pattern(choices: tuple[str, ...]) -> str:
    """The pattern a comma-separated string of enum values must match.

    The same rule as :func:`monogram_list_pattern`, for the same reason: a
    `multiple` field's *scalar* spelling is the comma-separated one -
    ``colors: "red,blue"`` is what a flag can write and what
    `phabfive.options.value_list` reads - and a JSON Schema ``enum`` can
    only describe one value at a time. So the list spelling keeps the enum
    and the scalar one becomes this.
    """
    one = "(?:" + "|".join(re.escape(choice) for choice in choices) + ")"

    return f"^[ \\t]*{one}(?:[ \\t]*,[ \\t]*{one})*[ \\t]*$"


def monogram_grammar_alternatives(prefixes: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Each application's monogram pattern, unanchored.

    `prefixes` restricts the answer to those application letters, the same
    way `phabfive.spec.references.is_monogram` does.
    """
    from phabfive.constants import MONOGRAMS

    return tuple(
        sorted(one for one in MONOGRAMS.values() if not prefixes or one[:1] in prefixes)
    )


@functools.lru_cache(maxsize=None)
def _order_choices() -> tuple[str, ...]:
    """Every spelling `order:` accepts. Genuinely static, so it is in the schema."""
    from phabfive.constants import MANIPHEST_ORDER_CHOICES

    return tuple(MANIPHEST_ORDER_CHOICES)


def _scalar_schema(field: Field) -> dict[str, Any]:
    """The schema for one value of one field, before `multiple` is applied."""
    kind = field.kind

    if kind is FieldKind.TEXT:
        return {"type": "string"}

    if kind is FieldKind.INT:
        return {"type": "integer"}

    if kind is FieldKind.BOOL:
        return {"type": "boolean"}

    if kind is FieldKind.TIME:
        return {
            "anyOf": [
                {"type": "string", "pattern": time_pattern()},
                {"type": "number", "minimum": 0},
            ]
        }

    if kind is FieldKind.ENUM:
        if field.multiple:
            # As MONOGRAM below: the scalar spelling of a list filter is the
            # comma-separated one, which an `enum` cannot express. The list
            # spelling keeps the enum, see `_one_of_list`.
            return {"type": "string", "pattern": enum_list_pattern(field.choices)}

        return {"enum": list(field.choices)}

    if kind is FieldKind.ORDER:
        return {"enum": list(_order_choices())}

    if kind is FieldKind.PATTERN:
        return {
            "type": "string",
            "pattern": transition_pattern(_PATTERN_ENTITIES.get(field.name)),
        }

    if kind is FieldKind.POLICY:
        return {"type": "string", "pattern": policy_pattern()}

    if kind is FieldKind.MONOGRAM:
        return {
            "type": "string",
            "pattern": monogram_list_pattern(field.monograms),
        }

    # USER, PROJECT, SPACE and INSTANCE_ENUM are all free text offline: what
    # they name is the instance's to answer, and a guessed enum here would
    # bless a value the server never had. A project colour is NOT one of
    # them - it is a FieldKind.ENUM above, and its ten keys are published.
    return {"type": "string"}


def property_schema(field: Field) -> dict[str, Any]:
    """The JSON Schema property one registry field becomes.

    `multiple` widens the value to "this, or a list of this"; `help` becomes
    the description an editor shows; `deprecated` becomes the 2020-12
    ``deprecated`` annotation, which is an annotation and not a constraint -
    a deprecated key still validates, exactly as the offline pass reports it
    as a warning rather than an error.
    """
    scalar = _scalar_schema(field)

    if field.multiple:
        schema: dict[str, Any] = {
            "anyOf": [scalar, {"type": "array", "items": _one_of_list(field, scalar)}]
        }
    else:
        schema = dict(scalar)

    if field.help:
        schema["description"] = field.help

    if field.default is not None:
        schema["default"] = field.default

    if field.deprecated:
        schema["deprecated"] = True
        schema["description"] = (
            f"{field.help} Deprecated: write {field.deprecated} instead."
            if field.help
            else f"Deprecated: write {field.deprecated} instead."
        )

    return schema


def _one_of_list(field: Field, scalar: dict[str, Any]) -> dict[str, Any]:
    """The item schema of a `multiple` field's list spelling.

    A monogram field's *scalar* spelling is a comma-separated list, so its
    list spelling holds one monogram per item rather than repeating that.
    """
    if field.kind is FieldKind.MONOGRAM:
        return {"type": "string", "pattern": monogram_pattern()}

    if field.kind is FieldKind.ENUM:
        return {"enum": list(field.choices)}

    return dict(scalar)


def _fields_schema(object_type: str, verb: str) -> dict[str, Any]:
    """Every declared field of one object type and verb, as an object schema.

    ``additionalProperties`` is false only for a pair in
    `registry.DECLARED_COMPLETE`, whose key set is finished. Everything else
    is **permissive**, because a registry that has declared two keys of an
    object cannot honestly call the third unknown - a project create schema
    describes `color:` and `icon:` and still accepts the `name:` nothing has
    declared yet. This is the same gate `validate._check_fields` applies, and
    the two have to agree or the oracle and the walk disagree over a spec
    neither is wrong about.
    """
    declared = fields_for(object_type, verb)

    if not declared:
        return {"type": "object", "additionalProperties": True}

    return {
        "type": "object",
        "properties": {field.name: property_schema(field) for field in declared},
        "additionalProperties": (object_type, verb) not in DECLARED_COMPLETE,
    }


def _metadata_schema() -> dict[str, Any]:
    """What a spec may say about itself.

    ``additionalProperties`` is true on purpose: an unknown `metadata:` key
    is a warning from the offline pass and is kept in `Metadata.extra`, so a
    schema that refused one would disagree with the loader.
    """
    return {
        "type": "object",
        "description": "What this file says about itself, never about one item.",
        "properties": {key: {"type": "string"} for key in METADATA_KEYS},
        "additionalProperties": True,
    }


def _envelope_properties(kind: Kind) -> dict[str, Any]:
    """The `spec:`, `kind:`, `metadata:` and `variables:` keys."""
    return {
        "spec": {
            "enum": sorted(SUPPORTED_SPEC_VERSIONS),
            "description": "Which revision of the spec format this file is written for.",
        },
        "kind": {
            "const": kind.value,
            "description": "The verb: what this spec asks for.",
        },
        "metadata": _metadata_schema(),
        VARIABLES_KEY: {
            "type": "object",
            "description": (
                "Names the body renders with. A value, or {default: <value>} "
                "for one a caller may override."
            ),
        },
    }


def _search_item_schema(object_type: Optional[str] = None) -> dict[str, Any]:
    """One `searches:` item: what to search, its banner, and its filters.

    `title:` and `description:` here are that one search's result banner.
    They are not `metadata.description`, which describes the file, and the
    two are never merged.
    """
    search: dict[str, Any] = (
        _fields_schema(object_type, "search")
        if object_type is not None
        else {"type": "object"}
    )

    return {
        "type": "object",
        "properties": {
            "type": {
                "enum": sorted(OBJECT_TYPES),
                "default": DEFAULT_SEARCH_TYPE,
                "description": "What this item searches.",
            },
            "title": {
                "type": ["string", "null"],
                "description": "The banner printed above this search's results.",
            },
            "description": {
                "type": ["string", "null"],
                "description": "A longer note about this one search.",
            },
            "search": search,
        },
        "additionalProperties": False,
    }


def _search_type_branches() -> list[dict[str, Any]]:
    """Per-`type:` narrowing of a `searches:` item's `search:` mapping.

    An object type with no declared search field gets no branch - the base
    schema already leaves `search:` a permissive object, and a branch saying
    the same thing is noise. The extra branch is for an item that omits
    `type:` altogether, which means the default type.
    """
    branches: list[dict[str, Any]] = []

    for object_type in sorted(OBJECT_TYPES):
        if not fields_for(object_type, "search"):
            continue

        branches.append(
            {
                "if": {
                    "required": ["type"],
                    "properties": {"type": {"const": object_type}},
                },
                "then": {
                    "properties": {"search": _fields_schema(object_type, "search")}
                },
            }
        )

    if fields_for(DEFAULT_SEARCH_TYPE, "search"):
        branches.append(
            {
                "if": {"not": {"required": ["type"]}},
                "then": {
                    "properties": {
                        "search": _fields_schema(DEFAULT_SEARCH_TYPE, "search")
                    }
                },
            }
        )

    return branches


#: A task's linking keys, which are `references.STRUCTURAL_KEYS` rather than
#: `Field`s and so have to be described here for `additionalProperties` to be
#: false on a task item.
_LINK_DESCRIPTIONS: dict[str, str] = {
    "parent": "One task this item hangs off: T123 or $local-id.",
    "parents": "Tasks this item hangs off: T123 or $local-id.",
    "subtasks": "Tasks that hang off this item: T123 or $local-id.",
}


def _create_item_schema(object_type: str) -> dict[str, Any]:
    """One created object: its declared fields, its local id, its children."""
    schema = _fields_schema(object_type, "create")
    properties: dict[str, Any] = dict(schema.get("properties") or {})

    properties[LOCAL_ID_KEY] = {
        "type": "string",
        "pattern": f"^{LOCAL_ID_PATTERN}$",
        "description": (
            "A name for this object inside this spec, referred to as $name. "
            "Never sent to the server."
        ),
    }

    if object_type == "task":
        properties[NESTED_KEY] = {
            "type": "array",
            "items": {"$ref": "#/$defs/task"},
            "description": "Child tasks, created with this one as their parent.",
        }

        # Not a monogram pattern: a `$local-id` is as legal here as T123, and
        # the offline pass is what tells the two apart and checks each
        for key, description in _LINK_DESCRIPTIONS.items():
            properties[key] = {
                "type": ["string", "array"],
                "items": {"type": "string"},
                "description": description,
            }

    schema["properties"] = properties

    return schema


def _create_schema() -> dict[str, Any]:
    """The whole document a create spec is."""
    properties = _envelope_properties(Kind.CREATE)
    defs: dict[str, Any] = {}

    for key, object_type in CREATE_OBJECT_KEYS:
        defs[object_type] = _create_item_schema(object_type)
        properties[key] = {
            "type": "array",
            "items": {"$ref": f"#/$defs/{object_type}"},
        }

    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "$defs": defs,
    }


def _search_schema() -> dict[str, Any]:
    """The whole document a search spec is, both spellings of one search."""
    properties = _envelope_properties(Kind.SEARCH)

    properties["searches"] = {
        "type": "array",
        "items": {"$ref": "#/$defs/search"},
    }

    # The legacy flat spelling: one search written at the top of the
    # document, which is how every template in the tree is written. The
    # loader refuses a document carrying both spellings; a JSON Schema that
    # said so as well would need a `not`/`required` pair for a mistake the
    # loader already answers by name, so it is left to the loader.
    item = _search_item_schema(DEFAULT_SEARCH_TYPE)
    for key in LEGACY_SEARCH_KEYS:
        properties[key] = item["properties"][key]

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "$defs": {"search": _search_item_schema()},
    }

    branches = _search_type_branches()
    if branches:
        schema["$defs"]["search"]["allOf"] = branches

    return schema


def build_schema(
    kind: KindLike, *, object_type: Optional[str] = None
) -> dict[str, Any]:
    """The JSON Schema 2020-12 document for one kind of spec.

    Parameters
    ----------
    kind : Kind or str
        ``"create"`` or ``"search"``.
    object_type : str, optional
        Return the schema for **one item** of this object type rather than
        the whole document: one `searches:` item for a search spec, one
        created object for a create spec. An object type the registry
        declares no field for comes back permissive, which is the honest
        answer rather than a schema that refuses every key.

    Returns
    -------
    dict
        A self-contained document. Nothing in phabfive reads it back;
        `phabfive.spec.validate` walks the registry directly, so this is for
        editors, other implementations and the test oracle.

    Raises
    ------
    PhabfiveInputException
        `kind` is not a kind, or `object_type` is not an object type.
    """
    resolved = as_kind(kind, where="kind argument")

    if object_type is not None:
        if resolved is Kind.SEARCH:
            body = _search_item_schema(object_type)
            title = f"One {object_type} search of a Phorge search spec"
        else:
            body = _create_item_schema(object_type)
            title = f"One {object_type} of a Phorge create spec"
        identifier = f"{resolved.value}/{object_type}"
    else:
        body = _search_schema() if resolved is Kind.SEARCH else _create_schema()
        title = f"Phorge {resolved.value} spec"
        identifier = resolved.value

    document: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"urn:phabfive:spec:phorge:v1alpha1:{identifier}",
        "title": title,
        "description": (
            "Generated from phabfive.spec.registry. Instance-defined values - "
            "priorities, statuses, project icons, spaces and policy names - "
            "are typed but not enumerated here on purpose: only the server "
            "knows them, so a schema that guessed could bless a value it "
            "never had. Project colours are enumerated, because Phorge fixes "
            "those keys in its own source."
        ),
    }
    document.update(body)

    return document
