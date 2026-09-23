# -*- coding: utf-8 -*-
"""The reference grammar, and the half of it that can be decided offline.

One grammar runs across every field of every spec. Six spellings, five of
which name something that already exists on the instance and are resolved by
the online pass, and one - ``$local-id`` - which names something *this* spec
creates and is therefore entirely a question about the file::

    T123, P45, K7, R9      an existing object, by monogram
    #platform              an existing project, by hashtag
    @alice, @me            an existing user
    PHID-TASK-...          an existing object, by PHID
    Platform               an existing object, by name; ambiguity is an error
    $platform              an object this spec creates, by local id

``$`` rather than ``@`` for a spec-local reference because ``@`` already means
user everywhere in phabfive and ``#`` means project, and deliberately not
Jinja's ``{{ ref.x }}``: variables render before anything is created while a
local reference resolves during apply, so two things that resolve at different
times must not look the same.

This module answers only the questions a file can answer on its own - what
shape a value is, which local ids a spec declares, and which references each
object makes. Whether ``@alice`` is a user is the online pass's question, and
`phabfive.spec.online` consumes what is here rather than restating it.

Library code: nothing here prints, prompts, exits, reads the environment or
opens a socket. `phabfive.constants` is imported lazily inside the one
function that needs it - not for a cycle, since `phabfive.constants` is a
leaf of plain literals that imports nothing of ours, but because the offline
pass is the no-dependency path, and a module-level import would make every
importer of this file pay for `phabfive.constants` whether it validates
anything or not.
"""

from __future__ import annotations

import dataclasses
import enum
import functools
import re
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - annotations only, never imported
    from phabfive.spec.envelope import Spec

__all__ = [
    "CREATE_OBJECT_KEYS",
    "LOCAL_ID_KEY",
    "LOCAL_ID_PATTERN",
    "NESTED_KEY",
    "REFERENCE_FIELDS",
    "RefKind",
    "Reference",
    "ReferenceField",
    "SpecObject",
    "classify_reference",
    "declared_local_ids",
    "is_local_id",
    "is_monogram",
    "iter_objects",
    "iter_references",
    "local_id",
    "monogram_grammar",
]

#: The key an item declares its spec-local name under.
LOCAL_ID_KEY = "id"

#: What a local id may be spelled with. Deliberately narrow: a local id is
#: written back as ``$name`` inside a string, so anything that could be
#: mistaken for the end of one - a space, a comma, a ``#`` - is refused.
LOCAL_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]*"

_LOCAL_ID_RE = re.compile(rf"^{LOCAL_ID_PATTERN}$")

#: The body keys that hold created objects, and the object type of each.
#: In file order, which is the order a report walks them in.
CREATE_OBJECT_KEYS: tuple[tuple[str, str], ...] = (
    ("tasks", "task"),
    ("projects", "project"),
    ("pastes", "paste"),
)

#: The key a task nests its children under.
NESTED_KEY = "tasks"

_PHID_PREFIX = "PHID-"


class RefKind(enum.Enum):
    """What one reference value names, from its spelling alone."""

    LOCAL = "local"
    """``$platform`` - an object this same spec creates."""

    PHID = "phid"
    """``PHID-TASK-...`` - an existing object, named unambiguously."""

    HASHTAG = "hashtag"
    """``#platform`` - an existing project, by hashtag."""

    USER = "user"
    """``@alice``, ``@me`` - an existing user."""

    MONOGRAM = "monogram"
    """``T123``, ``P45``, ``K7``, ``R9`` - an existing object, by monogram."""

    NAME = "name"
    """Anything else - an existing object by name, where ambiguity is an error."""


@dataclasses.dataclass(frozen=True)
class ReferenceField:
    """One key of one object type that holds references.

    Phase 1 declares these here rather than in `phabfive.spec.registry`,
    because the registry declares search fields only - the create fields
    arrive with their object type in a later phase, and this table moves into
    it then. Until it does, this is the one place that says which create keys
    hold a reference.

    Attributes
    ----------
    name
        The key, e.g. ``"parents"``.
    multiple
        Whether the key takes a list of references rather than one.
    monograms
        The monogram prefixes the key accepts, e.g. ``("T",)`` for
        ``parents:``. Empty means a monogram is not a meaningful value for
        this key, so nothing is checked against the monogram grammar.
    """

    name: str
    multiple: bool = False
    monograms: tuple[str, ...] = ()


_TASK_REFERENCE_FIELDS: tuple[ReferenceField, ...] = (
    ReferenceField("parents", multiple=True, monograms=("T",)),
    ReferenceField("subtasks", multiple=True, monograms=("T",)),
    ReferenceField("projects", multiple=True),
    ReferenceField("subscribers", multiple=True),
    ReferenceField("assignment"),
    ReferenceField("space"),
)

#: Which keys of each created object type hold references.
REFERENCE_FIELDS: Mapping[str, tuple[ReferenceField, ...]] = {
    "task": _TASK_REFERENCE_FIELDS,
    # A project spec and a paste spec have no declared fields yet, so nothing
    # inside one is read as a reference. Their sections still validate their
    # envelope, their variables and their local ids.
    "project": (),
    "paste": (),
}


@dataclasses.dataclass(frozen=True)
class SpecObject:
    """One item of a spec, with the path a report names it by.

    Attributes
    ----------
    path
        Where it is, as a human can find it: ``"tasks[0]"``,
        ``"tasks[0].tasks[2]"``, ``"searches[1]"``.
    object_type
        ``"task"``, ``"project"``, ``"paste"`` - or ``"search"`` for one item
        of a search spec, whose own ``type:`` says what it searches.
    data
        The item's mapping, read-only like the rest of a spec body.
    """

    path: str
    object_type: str
    data: Mapping[str, Any]


@dataclasses.dataclass(frozen=True)
class Reference:
    """One value, in one field of one object, that names something.

    Attributes
    ----------
    object
        The owning object's path, as `SpecObject.path` spells it.
    field
        The key, with an index when the key takes a list: ``"parents[0]"``.
    value
        The value exactly as written.
    kind
        What it names, see :class:`RefKind`.
    object_type
        The owning object's type, which is what decides where a name is
        looked up online.
    """

    object: str
    field: str
    value: str
    kind: RefKind
    object_type: str


@functools.lru_cache(maxsize=None)
def monogram_grammar() -> re.Pattern[str]:
    """The compiled ``T123``/``P45``/``K7``/``R9`` grammar, every application.

    `phabfive.constants` is imported here rather than at module level; see
    the module docstring for why.
    """
    from phabfive.constants import MONOGRAMS

    alternatives = "|".join(sorted(MONOGRAMS.values()))
    return re.compile(f"^(?:{alternatives})$")


def is_monogram(value: object, *, prefixes: tuple[str, ...] = ()) -> bool:
    """Whether a value is a well-formed monogram.

    Parameters
    ----------
    value
        What was written. Anything that is not a string is not a monogram.
    prefixes
        Restrict to these application letters, e.g. ``("T",)`` for a task.
        Empty accepts any application's monogram.
    """
    if not isinstance(value, str):
        return False

    if not monogram_grammar().match(value):
        return False

    return not prefixes or value[:1] in prefixes


def local_id(value: object) -> Optional[str]:
    """The name a ``$local-id`` reference carries, or None.

    ``"$platform"`` is ``"platform"``. A bare ``"$"``, a ``"$ platform"`` and
    anything that is not a string are not local references and answer None -
    they are reported by whoever asked, as a name or as the wrong type.
    """
    if not isinstance(value, str) or not value.startswith("$"):
        return None

    name = value[1:]

    return name if _LOCAL_ID_RE.match(name) else None


def is_local_id(value: object) -> bool:
    """Whether a value is a well-formed local id, as ``id:`` declares one."""
    return isinstance(value, str) and bool(_LOCAL_ID_RE.match(value))


def classify_reference(value: object) -> Optional[RefKind]:
    """What one value names, from its spelling alone.

    Returns None for anything that is not a non-empty string: a reference is
    text, and a number or a mapping where one belongs is a type problem for
    the caller to report rather than a reference of some sixth kind.

    A ``"$"`` with nothing usable after it is `RefKind.NAME`, not
    `RefKind.LOCAL` - the caller reports it against the local ids the spec
    declares, and "no object is called that" is the sentence a person can act
    on.
    """
    if not isinstance(value, str):
        return None

    if not value.strip():
        return None

    if value.startswith("$"):
        return RefKind.LOCAL if local_id(value) else RefKind.NAME

    if value.startswith(_PHID_PREFIX):
        return RefKind.PHID

    if value.startswith("#") and len(value) > 1:
        return RefKind.HASHTAG

    if value.startswith("@") and len(value) > 1:
        return RefKind.USER

    if is_monogram(value):
        return RefKind.MONOGRAM

    return RefKind.NAME


def _nested(item: Mapping[str, Any], path: str) -> Iterator[SpecObject]:
    """A task's children, depth first, in file order."""
    children = item.get(NESTED_KEY)

    if not isinstance(children, (list, tuple)):
        return

    for index, child in enumerate(children):
        if not isinstance(child, Mapping):
            continue

        child_path = f"{path}.{NESTED_KEY}[{index}]"
        yield SpecObject(path=child_path, object_type="task", data=child)
        yield from _nested(child, child_path)


def iter_objects(spec: "Spec") -> Iterator[SpecObject]:
    """Every item a spec holds, in document order, children after their parent.

    A create spec yields its ``tasks:``, ``projects:`` and ``pastes:`` and
    every task nested under a task. A search spec yields its ``searches:``
    items, whose ``object_type`` is ``"search"``: what one searches is its own
    ``type:`` key, not the section it is in.

    An item that is not a mapping is skipped rather than guessed at - the
    schema pass reports it, and walking into it would report the same mistake
    a second time under a made-up path.
    """
    from phabfive.spec.envelope import Kind

    if spec.kind is Kind.SEARCH:
        for index, item in enumerate(spec.items("search")):
            if isinstance(item, Mapping):
                yield SpecObject(
                    path=f"searches[{index}]", object_type="search", data=item
                )
        return

    for key, object_type in CREATE_OBJECT_KEYS:
        for index, item in enumerate(spec.items(key)):
            if not isinstance(item, Mapping):
                continue

            path = f"{key}[{index}]"
            yield SpecObject(path=path, object_type=object_type, data=item)

            if object_type == "task":
                yield from _nested(item, path)


def _values(item: Mapping[str, Any], declared: ReferenceField) -> list[tuple[str, Any]]:
    """One reference field's values, each with the field path naming it."""
    value = item.get(declared.name)

    if value is None:
        return []

    if declared.multiple and isinstance(value, (list, tuple)):
        return [(f"{declared.name}[{index}]", one) for index, one in enumerate(value)]

    return [(declared.name, value)]


def iter_references(spec: "Spec") -> Iterator[Reference]:
    """Every reference a spec makes, in document order.

    Only the keys `REFERENCE_FIELDS` declares are read, so a key nothing
    resolves is not guessed at. A value that is not a string is skipped: it
    is a type problem, which the schema pass reports once.

    This is what both validation layers walk - the offline pass to check that
    every ``$local-id`` names something the spec declares, and the online pass
    to ask the server about each distinct value of each kind exactly once.
    """
    for spec_object in iter_objects(spec):
        for declared in REFERENCE_FIELDS.get(spec_object.object_type, ()):
            for field_path, value in _values(spec_object.data, declared):
                kind = classify_reference(value)

                if kind is None:
                    continue

                yield Reference(
                    object=spec_object.path,
                    field=field_path,
                    value=value,
                    kind=kind,
                    object_type=spec_object.object_type,
                )


def declared_local_ids(spec: "Spec") -> list[tuple[str, Any]]:
    """Every ``id:`` a spec declares, as ``(object path, value)``, in order.

    The value is returned exactly as written rather than filtered, so the
    caller reports a duplicate, a misspelling and a non-string separately
    instead of all three vanishing into "not declared".
    """
    return [
        (spec_object.path, spec_object.data[LOCAL_ID_KEY])
        for spec_object in iter_objects(spec)
        if LOCAL_ID_KEY in spec_object.data
    ]
