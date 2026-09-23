# -*- coding: utf-8 -*-
"""One declaration per spec field, and everything that derives from it.

A spec field is declared exactly once, as a :class:`Field` in :data:`FIELDS`.
The accepted key set per object type, the Conduit constraint a key is sent as,
whether a key is applied client-side instead, and the CLI flag that carries the
same value all come from that single declaration - so they cannot drift apart
the way ``phabfive.constants``'s hand-written key set and the CLI's
``get_param`` calls once did (#295).

This module imports the standard library and :mod:`phabfive.exceptions` only.
``Maniphest._load_search_config`` asks it which keys a search template may
use, and the offline half of spec validation leans on it, so it has to stay
cheap and free of every import that would make either of those expensive.
"""

import enum
from dataclasses import dataclass
from dataclasses import field as _field
from types import MappingProxyType
from typing import Mapping, Optional

from phabfive.exceptions import PhabfiveInputException

__all__ = [
    "FIELDS",
    "OBJECT_TYPES",
    "VERBS",
    "Field",
    "FieldKind",
    "cli_flags",
    "constraint_for",
    "field_by_name",
    "fields_for",
    "spec_keys",
]


class FieldKind(enum.Enum):
    """What a field's value is, which decides how it is checked and by whom.

    The two layers of validation read this: ``TEXT`` through ``PATTERN`` can be
    checked offline from the value alone, while ``USER``, ``PROJECT``,
    ``SPACE``, ``POLICY``, ``MONOGRAM`` and ``INSTANCE_ENUM`` need the server
    and are therefore only shape-checked offline.
    """

    TEXT = "text"
    INT = "int"
    BOOL = "bool"
    TIME = "time"  # "1h", "7d", "2w" - parse_time_to_days in maniphest/utils.py
    ENUM = "enum"  # genuinely static; choices on the Field
    ORDER = "order"  # "<field>[:asc|:desc]"
    PATTERN = "pattern"  # transition grammar: from:/in:/been:/never:/to:
    USER = "user"  # @me, @alice, alice, PHID-USER-...
    PROJECT = "project"  # #hashtag, Name, PHID-PROJ-..., wildcards
    SPACE = "space"  # S1, name, pattern
    POLICY = "policy"  # POLICY_KEYWORDS, #project, @user, PHID
    MONOGRAM = "monogram"  # T123, P45, K7, R9
    INSTANCE_ENUM = "instance-enum"  # priority, status, icon, color - online only


# The object types a field may apply to. Declared rather than derived from
# FIELDS, because an object type with no fields declared yet is still a real
# one - Phase 1 declares task search fields and nothing else.
OBJECT_TYPES = frozenset({"task", "project", "paste", "passphrase"})

# What a spec does with an object. One registry serves both, so a create-only
# key and a search-only key can share a name without sharing a declaration.
VERBS = frozenset({"search", "create"})

_NO_CONSTRAINTS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class Field:
    """One spec key, declared once.

    Attributes
    ----------
    name
        The spec key exactly as a spec spells it, e.g. ``"created-after"``.
    kind
        What the value is, see :class:`FieldKind`.
    objects
        The object types that accept this key, e.g. ``{"task"}``.
    verbs
        ``{"search"}``, ``{"create"}``, or both.
    cli
        The flag carrying the same value, e.g. ``"--created-after"``, or
        ``None`` when the CLI has no flag for it (``text_query`` is a
        positional argument, not a flag).
    constraint
        The Conduit ``*.search`` constraint the value is sent as, or ``None``
        when the value is applied client-side and never sent.
    constraints
        Per-object-type override of ``constraint``: ``maniphest.search`` names
        the author filter ``authorPHIDs`` while ``paste.search`` names it
        ``authors``. Read it through :func:`constraint_for`, never directly.
    multiple
        Whether a list is accepted as well as a comma-separated string.
    monograms
        For ``FieldKind.MONOGRAM``, the application letters the key accepts,
        e.g. ``("T",)`` for a key that takes task ids. Empty accepts any
        application's monogram, which is almost never what a key means -
        ``include: P45`` is a paste, and `maniphest search` refuses it with
        "Expected format: T123". Mirrors
        ``phabfive.spec.references.ReferenceField.monograms``.
    choices
        The accepted values, for ``FieldKind.ENUM`` only. Deliberately empty
        for everything an instance decides - baking a guessed status or
        priority into a schema would bless a value the server never named.
    default
        What the command uses when the key is absent, for documentation.
    help
        One line, for the docs table and the flag's help.
    deprecated
        The replacement spelling when this key is deprecated but still works,
        e.g. ``"status: any"``. A deprecated key is a warning, not an error.
    since
        The spec version that introduced the key.
    """

    name: str
    kind: FieldKind
    objects: frozenset[str]
    verbs: frozenset[str]
    cli: Optional[str] = None
    constraint: Optional[str] = None
    # A MappingProxyType is immutable, but it is also *unhashable*, and an
    # unhashable default is what dataclasses on 3.11 refuses as mutable.
    # 3.10 and 3.14 accept it, so only the middle of the supported range
    # broke, and only in CI. The factory hands back the one shared proxy,
    # so a Field still costs nothing.
    constraints: Mapping[str, str] = _field(default_factory=lambda: _NO_CONSTRAINTS)
    multiple: bool = False
    monograms: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    default: object = None
    help: str = ""
    deprecated: Optional[str] = None
    since: str = "phorge/v1alpha1"


_TASK = frozenset({"task"})
_SEARCH = frozenset({"search"})


# Phase 1 declares exactly the keys that exist today - the 22 that
# phabfive.constants used to list by hand. A field declared here but not read by
# the command would be accepted by the loader and silently ignored, which is
# the drift this registry exists to end; the missing constraints arrive with
# their command in #478 and #479.
#
# The spelling quirks are the registry's job to describe, not to fix:
# "text_query" is the one key with an underscore while every other key is
# hyphenated, and an alias mechanism is a later phase's problem.
FIELDS: tuple[Field, ...] = (
    Field(
        name="text_query",
        kind=FieldKind.TEXT,
        objects=_TASK,
        verbs=_SEARCH,
        # The CLI takes this as a positional argument, so there is no flag.
        cli=None,
        constraint="query",
        help="Free-text search in task title and description.",
    ),
    Field(
        name="tag",
        kind=FieldKind.PROJECT,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--tag",
        constraint="projects",
        help="Project, workboard, hashtag, ID or PHID; wildcards allowed.",
    ),
    Field(
        name="include",
        kind=FieldKind.MONOGRAM,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--include",
        # Task ids only: `phabfive.cli.maniphest.parse_task_id_list` exits
        # with "Expected format: T123" for anything else, so a spec must not
        # be told a paste monogram is fine here
        monograms=("T",),
        # Fetched by id and merged in afterwards, never sent as a constraint.
        constraint=None,
        multiple=True,
        help="Tasks to force into the results whatever the filters say.",
    ),
    Field(
        name="exclude",
        kind=FieldKind.MONOGRAM,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--exclude",
        # Task ids only: `phabfive.cli.maniphest.parse_task_id_list` exits
        # with "Expected format: T123" for anything else, so a spec must not
        # be told a paste monogram is fine here
        monograms=("T",),
        # Removed from the result set before the limit, in Python.
        constraint=None,
        multiple=True,
        help="Tasks to drop from the results even when the filters match.",
    ),
    Field(
        name="assigned",
        kind=FieldKind.USER,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--assigned",
        constraint="assigned",
        help="Assignee: a username, @me, or a user PHID.",
    ),
    Field(
        name="author",
        kind=FieldKind.USER,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--author",
        constraint="authorPHIDs",
        # maniphest.search and paste.search disagree on this one name, and a
        # wrong key fails with ERR-INVALID-CONSTRAINT. Declared here so the
        # quirk is described once; paste is not a searchable object type yet.
        constraints=MappingProxyType({"paste": "authors"}),
        help="Task author: a username, @me, or a user PHID.",
    ),
    Field(
        name="space",
        kind=FieldKind.SPACE,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--space",
        constraint="spaces",
        help="Space monogram, name or pattern; wildcards allowed.",
    ),
    Field(
        name="created-after",
        kind=FieldKind.TIME,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--created-after",
        constraint="createdStart",
        help="Tasks created within TIME, e.g. 1h, 7d, 2w.",
    ),
    Field(
        name="created-before",
        kind=FieldKind.TIME,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--created-before",
        constraint="createdEnd",
        help="Tasks created more than TIME ago.",
    ),
    Field(
        name="updated-after",
        kind=FieldKind.TIME,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--updated-after",
        constraint="modifiedStart",
        help="Tasks updated within TIME, e.g. 1h, 7d, 2w.",
    ),
    Field(
        name="updated-before",
        kind=FieldKind.TIME,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--updated-before",
        constraint="modifiedEnd",
        help="Tasks updated more than TIME ago.",
    ),
    Field(
        name="visible-to",
        kind=FieldKind.POLICY,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--visible-to",
        # maniphest.search has no policy constraint: the policy is resolved to
        # a PHID and every fetched task is compared against it in Python.
        constraint=None,
        help="Only tasks whose view policy is exactly this.",
    ),
    Field(
        name="editable-by",
        kind=FieldKind.POLICY,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--editable-by",
        constraint=None,
        help="Only tasks whose edit policy is exactly this.",
    ),
    Field(
        name="column",
        kind=FieldKind.PATTERN,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--column",
        # Column transitions are read out of each task's history, in Python.
        constraint=None,
        help="Column transition filter, e.g. in:Backlog or never:Done.",
    ),
    Field(
        name="priority",
        kind=FieldKind.PATTERN,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--priority",
        # Priority transitions are read out of each task's history, in Python.
        constraint=None,
        help="Priority transition filter, e.g. in:High or from:Low.",
    ),
    Field(
        name="status",
        kind=FieldKind.PATTERN,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--status",
        # Only the scope half of the pattern - open, closed, any - reaches the
        # server as "statuses"; the transition conditions are applied over each
        # task's history in Python.
        constraint="statuses",
        default="open",
        help="open, closed, any, or transition patterns ANDed with +.",
    ),
    Field(
        name="all",
        kind=FieldKind.BOOL,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--all",
        # Lifts the open-only default, which is what "status: any" says.
        constraint=None,
        default=False,
        deprecated="status: any",
        help="Deprecated spelling of status: any.",
    ),
    Field(
        name="show-history",
        kind=FieldKind.BOOL,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--show-history",
        constraint=None,
        default=False,
        help="Display transition history.",
    ),
    Field(
        name="show-metadata",
        kind=FieldKind.BOOL,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--show-metadata",
        constraint=None,
        default=False,
        help="Display filter match metadata.",
    ),
    Field(
        name="show-policy",
        kind=FieldKind.BOOL,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--show-policy",
        constraint=None,
        default=False,
        help="Display each task's policies.",
    ),
    Field(
        name="limit",
        kind=FieldKind.INT,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--limit",
        # Applied after ordering and after the post-filters, in Python, so the
        # limit keeps the top N of the tasks that actually matched.
        constraint=None,
        default=100,
        help="Maximum results to return, 0 for all.",
    ),
    Field(
        name="order",
        kind=FieldKind.ORDER,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--order",
        # The builtin order is a top-level maniphest.search parameter rather
        # than a constraint, and the results are sorted again client-side.
        constraint=None,
        help="Sort as <field>[:asc|:desc], e.g. updated:desc.",
    ),
)


def _check(object_type: str, verb: str) -> None:
    """Refuse an object type or verb that does not exist.

    A caller asking for one is a programming error rather than bad user data,
    which is why this is PhabfiveInputException and not a Problem.
    """
    if object_type not in OBJECT_TYPES:
        raise PhabfiveInputException(
            f"Unknown object type {object_type!r}, expected one of: "
            f"{', '.join(sorted(OBJECT_TYPES))}"
        )
    if verb not in VERBS:
        raise PhabfiveInputException(
            f"Unknown verb {verb!r}, expected one of: {', '.join(sorted(VERBS))}"
        )


def fields_for(object_type: str, verb: str) -> tuple[Field, ...]:
    """Every field one object type accepts for one verb, in declaration order.

    Declaration order is what makes a validation report deterministic, so the
    tuple order is part of the contract.
    """
    _check(object_type, verb)
    return tuple(
        field
        for field in FIELDS
        if object_type in field.objects and verb in field.verbs
    )


def spec_keys(object_type: str, verb: str) -> frozenset[str]:
    """The keys a spec may use for one object type and verb."""
    return frozenset(field.name for field in fields_for(object_type, verb))


def field_by_name(name: str, object_type: str, verb: str) -> Optional[Field]:
    """The field one key names, or None when the key is not declared."""
    for field in fields_for(object_type, verb):
        if field.name == name:
            return field
    return None


def cli_flags(object_type: str, verb: str) -> frozenset[str]:
    """The CLI flags of the fields that have one.

    A field with ``cli=None`` carries no flag and is left out, so this is a
    subset of the command's options rather than a mirror of them.
    """
    return frozenset(
        field.cli for field in fields_for(object_type, verb) if field.cli is not None
    )


def constraint_for(field: Field, object_type: str) -> Optional[str]:
    """The Conduit constraint this field is sent as for one object type.

    ``None`` means the value is applied client-side and never sent. Callers
    read the constraint through this function rather than off the Field,
    because the name differs per object type for at least one field.
    """
    _check(object_type, "search")
    return field.constraints.get(object_type, field.constraint)
