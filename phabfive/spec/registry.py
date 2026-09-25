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
    "DECLARED_COMPLETE",
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
    ``SPACE``, ``POLICY``, ``MONOGRAM``, ``COMMIT`` and ``INSTANCE_ENUM`` need
    the server and are therefore only shape-checked offline.
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
    COMMIT = "commit"  # rGUNNAR7d7fc2c, R1:7d7fc2c, 7d7fc2c, PHID-CMIT-...
    # priority, status and icon - online only. NOT colour: the colour keys
    # are fixed in Phorge's own source, so colour is an ENUM with choices.
    INSTANCE_ENUM = "instance-enum"


# The object types a field may apply to. Declared rather than derived from
# FIELDS, because an object type with no fields declared yet is still a real
# one - Phase 1 declares task search fields and nothing else.
OBJECT_TYPES = frozenset({"task", "project", "paste", "passphrase"})

# What a spec does with an object. One registry serves both, so a create-only
# key and a search-only key can share a name without sharing a declaration.
VERBS = frozenset({"search", "create"})

_NO_CONSTRAINTS: Mapping[str, Optional[str]] = MappingProxyType({})


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
        ``authors``. A value of ``None`` means *this* object type applies the
        key client-side and sends nothing, which is how one `text_query`
        serves both `paste.search`'s ``query`` constraint and the passphrase
        walk that has no constraints at all. Read it through
        :func:`constraint_for`, never directly.
    lifts
        For ``FieldKind.PATTERN`` only: the transition condition types whose
        answer is the object's *current* state, and which the server can
        therefore answer with ``constraint`` instead of phabfive reading every
        task's history. ``("in",)`` everywhere today. Empty means the whole
        pattern is applied client-side.

        Declared rather than commented because moving a filter server-side
        silently changes results when the condition is not pure current-state
        equality: ``been:high`` and ``from:low`` are about a task's past and a
        ``priorities`` constraint would answer a different question. A lifted
        pattern is still re-checked in Python, so the constraint narrows what
        is fetched and never decides what matches.
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
    constraints: Mapping[str, Optional[str]] = _field(
        default_factory=lambda: _NO_CONSTRAINTS
    )
    lifts: tuple[str, ...] = ()
    multiple: bool = False
    monograms: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    default: object = None
    help: str = ""
    deprecated: Optional[str] = None
    since: str = "phorge/v1alpha1"


_TASK = frozenset({"task"})
_PROJECT = frozenset({"project"})
# Paste search declares no key of its own: `text_query`, `author` and `limit`
# are all shared, so there is no `_PASTE` to go with these. Paste *create*
# does declare two, and shares four more with a task.
_PASTE = frozenset({"paste"})
_PASSPHRASE = frozenset({"passphrase"})
_SEARCH = frozenset({"search"})
_CREATE = frozenset({"create"})

# The create-side shared key sets. A key two object types spell the same way
# and mean the same thing by is one Field carrying both, exactly as
# `text_query` is on the search side - which is what keeps "the web UI calls
# this Name on a task and on a paste alike" a single declaration rather than
# a comment in two places.
_TASK_PASTE = frozenset({"task", "paste"})
_TASK_PROJECT = frozenset({"task", "project"})

# Every object type a create spec creates. `passphrase` is deliberately
# absent: Phorge exposes no `passphrase.edit`, so a `passphrases:` section is
# refused by name - see `phabfive.spec.references.UNCREATABLE_OBJECT_KEYS`.
_CREATED = frozenset({"task", "project", "paste"})

# Every object type a spec may search. A key all four spell the same way and
# mean the same thing by - `text_query`, `limit` - is one Field carrying this
# rather than four Fields with one name, which is also what keeps a
# per-endpoint constraint quirk in one place. See `Field.constraints`.
_SEARCHED = frozenset({"task", "project", "paste", "passphrase"})

# The colours a project can be, restated rather than imported.
# `phabfive.constants.PROJECT_COLORS` is the source of truth, but this
# module is the cheap, dependency-free bottom of the spec stack and
# `tests/test_spec_registry.py::test_the_registry_imports_only_the_standard_library`
# refuses `phabfive.constants` in its import graph - while `choices` has to
# be a literal, because FIELDS is built as this module is imported.
# `tests/test_spec_resolvers.py` asserts the two lists are equal, so the
# restatement cannot drift: adding a colour to constants turns it red.
# As `_PROJECT_COLORS`: `phabfive.constants.PROJECT_STATUS_CHOICES` is the
# source of truth and this module may not import it, so the two are pinned
# equal by `tests/test_search_constraints_apps.py`.
_PROJECT_STATUSES: tuple[str, ...] = ("active", "archived", "any")

_PROJECT_COLORS: tuple[str, ...] = (
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "indigo",
    "violet",
    "pink",
    "grey",
    "checkered",
)


# Task search declares exactly the keys that exist today - the 22 that
# phabfive.constants used to list by hand, plus the constraints #478 wired up.
# A field declared here but not read by the command would be accepted by the
# loader and silently ignored, which is the drift this registry exists to end.
#
# That rule is also why project, paste and user search declare nothing yet,
# although #479 gave their commands the constraints and `--order` that
# `maniphest search` has. What reads a *spec's* search keys for those types is
# `phabfive.search.dispatch`, whose per-type builders are the only things that
# can turn a key into that method's arguments; until a key is wired up there,
# declaring it here would tell a spec writer about a filter nothing applies.
# `dispatch.accepted_keys` carries the interim key set and switches to this
# registry the moment it declares one - which is what
# `tests/test_spec_search_apps.py` guards.
#
# Declaration order is the report order, so a block is appended and never
# reordered. Each object type gets its own block, marked with a comment.
#
# The spelling quirks are the registry's job to describe, not to fix:
# "text_query" is the one key with an underscore while every other key is
# hyphenated, and an alias mechanism is a later phase's problem.
FIELDS: tuple[Field, ...] = (
    Field(
        name="text_query",
        kind=FieldKind.TEXT,
        # Every searchable object type takes free text, spelled the same way
        # and meaning the same thing, so it is one declaration rather than
        # four. Only where it *goes* differs, which `constraints` says.
        objects=_SEARCHED,
        verbs=_SEARCH,
        # The CLI takes this as a positional argument, so there is no flag.
        cli=None,
        constraint="query",
        # There is no `passphrase.search`, only the legacy `passphrase.query`,
        # which takes no constraints: the name filter is applied in Python
        # over every credential the token can see.
        constraints=MappingProxyType({"passphrase": None}),
        help="Free-text search in the title and description.",
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
        objects=frozenset({"task", "paste"}),
        verbs=_SEARCH,
        cli="--author",
        constraint="authorPHIDs",
        # maniphest.search and paste.search disagree on this one name, and a
        # wrong key fails with ERR-INVALID-CONSTRAINT. Declared here so the
        # quirk is described once, which is the whole reason `constraints`
        # exists.
        constraints=MappingProxyType({"paste": "authors"}),
        help="Author: a username, @me, or a user PHID.",
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
        name="commits",
        # Declared here so the generated key tables carry it. The feature
        # landed on main while Phase 4 was in flight (#463) and brought its
        # own FieldKind, ReferenceField and resolver, but not this - so
        # `commits:` worked while `docs/phorge-spec.md` did not mention it,
        # which is the drift the registry exists to make impossible.
        #
        # `commits.add` like every list in a create spec: a commit attached
        # to a task that already has commits must not discard them.
        kind=FieldKind.COMMIT,
        objects=_TASK,
        verbs=_CREATE,
        cli="--attach",
        multiple=True,
        help="Commits to attach, by rCALLSIGNhash, R1:hash, a bare hash or a PHID.",
    ),
    Field(
        name="column",
        kind=FieldKind.PATTERN,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--column",
        # A transition filter is read out of each task's history, in Python -
        # but a pattern made only of `in:` conditions asks about the *current*
        # column, which `columnPHIDs` answers, so that one shape is narrowed
        # by the server first and re-checked in Python. See `lifts`.
        constraint="columnPHIDs",
        lifts=("in",),
        help="Column transition filter, e.g. in:Backlog or never:Done.",
    ),
    Field(
        name="priority",
        kind=FieldKind.PATTERN,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--priority",
        # As `column`: `in:High` is current state and becomes the `priorities`
        # constraint, while `been:`, `never:`, `from:`, `to:`, `raised` and
        # `lowered` are history and stay in Python.
        constraint="priorities",
        lifts=("in",),
        help="Priority transition filter, e.g. in:High or from:Low.",
    ),
    Field(
        name="status",
        kind=FieldKind.PATTERN,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--status",
        # The scope half of the pattern - open, closed, any - reaches the
        # server as "statuses", and so does an `in:` condition, which names a
        # current status rather than a transition. Every other condition is
        # applied over each task's history in Python.
        constraint="statuses",
        lifts=("in",),
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
        objects=frozenset({"task", "project"}),
        verbs=_SEARCH,
        cli="--show-policy",
        constraint=None,
        default=False,
        help="Display each result's policies.",
    ),
    Field(
        name="limit",
        kind=FieldKind.INT,
        objects=_SEARCHED,
        verbs=_SEARCH,
        cli="--limit",
        # Applied after ordering and after the post-filters, in Python, so the
        # limit keeps the top N of the records that actually matched. Never a
        # constraint, for any of the four.
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
    # --- task, search: the constraints maniphest.search answers and
    # phabfive used to leave on the table (#478) ---------------------------
    #
    # Every one of these is a filter the server applies, so the task never
    # crosses the wire - which is the difference between a query and a full
    # walk filtered in Python. Appended as a block rather than interleaved
    # with the keys above, because `fields_for` returns declaration order and
    # a validation report reads in that order.
    Field(
        name="ids",
        kind=FieldKind.MONOGRAM,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--ids",
        monograms=("T",),
        constraint="ids",
        multiple=True,
        # Not `include`: these tasks are what is searched *for*, so every
        # other filter still applies to them, and the result is the
        # intersection. `include` is the opposite - it bypasses the filters.
        help="Only these tasks, with every other filter still applied.",
    ),
    Field(
        name="phids",
        kind=FieldKind.TEXT,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--phids",
        constraint="phids",
        multiple=True,
        help="Only these task PHIDs, with every other filter still applied.",
    ),
    Field(
        name="subscriber",
        kind=FieldKind.USER,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--subscriber",
        constraint="subscribers",
        multiple=True,
        help="Tasks a user is subscribed to: a username, @me, or a PHID.",
    ),
    Field(
        name="subtype",
        kind=FieldKind.TEXT,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--subtype",
        constraint="subtypes",
        multiple=True,
        # Instance configuration (`maniphest.subtypes`), and no Conduit method
        # reports the list, so the value is passed through as written rather
        # than checked against a set phabfive would have to guess at.
        help="Task subtype key, e.g. 'default' or one this instance defines.",
    ),
    Field(
        name="parent",
        kind=FieldKind.MONOGRAM,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--parent",
        monograms=("T",),
        constraint="parentIDs",
        multiple=True,
        help="Subtasks of these tasks.",
    ),
    Field(
        name="subtask",
        kind=FieldKind.MONOGRAM,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--subtask",
        monograms=("T",),
        constraint="subtaskIDs",
        multiple=True,
        help="Parents of these tasks.",
    ),
    Field(
        name="has-parents",
        kind=FieldKind.BOOL,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--has-parents",
        constraint="hasParents",
        help="Only tasks that are a subtask of something.",
    ),
    Field(
        name="has-subtasks",
        kind=FieldKind.BOOL,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--has-subtasks",
        constraint="hasSubtasks",
        help="Only tasks that have subtasks.",
    ),
    Field(
        name="closed-by",
        kind=FieldKind.USER,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--closed-by",
        constraint="closerPHIDs",
        multiple=True,
        help="Tasks closed by a user: a username, @me, or a user PHID.",
    ),
    Field(
        name="closed-after",
        kind=FieldKind.TIME,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--closed-after",
        constraint="closedStart",
        help="Tasks closed within TIME, e.g. 1h, 7d, 2w.",
    ),
    Field(
        name="closed-before",
        kind=FieldKind.TIME,
        objects=_TASK,
        verbs=_SEARCH,
        cli="--closed-before",
        constraint="closedEnd",
        help="Tasks closed more than TIME ago.",
    ),
    # --- project, search -------------------------------------------------
    #
    # The key set `phabfive.search.dispatch` builds `Project.search`'s
    # arguments from, which is what makes ("project", "search") complete: a
    # key outside this block reaches nothing, so a spec naming one is an
    # error rather than a filter that quietly does not happen.
    #
    # `text_query`, `limit` and `show-policy` are declared above, on the
    # fields the object types share.
    Field(
        name="members",
        kind=FieldKind.USER,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--member",
        constraint="members",
        multiple=True,
        help="Projects one of these users is a member of.",
    ),
    Field(
        name="parents",
        kind=FieldKind.PROJECT,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--parent",
        constraint="parents",
        multiple=True,
        help="Direct subprojects and milestones of these projects.",
    ),
    Field(
        name="ancestors",
        kind=FieldKind.PROJECT,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--ancestor",
        constraint="ancestors",
        multiple=True,
        help="Everything anywhere beneath these projects.",
    ),
    Field(
        name="milestones",
        kind=FieldKind.BOOL,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--milestones",
        constraint="isMilestone",
        # Tri-state: absent is "both", which is neither of the two booleans,
        # so there is no default.
        help="true lists only milestones, false only what is not one.",
    ),
    Field(
        name="status",
        # Not the task `status`, which is a transition pattern over a task's
        # history. A project is active or archived and that is all, so this
        # is a plain enum of three - the third, "any", being no filter.
        kind=FieldKind.ENUM,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--status",
        constraint="status",
        choices=_PROJECT_STATUSES,
        default="active",
        help="active, archived, or any.",
    ),
    Field(
        name="icons",
        # As a `projects:` item's `icon`: the icon set is `projects.icons`
        # instance configuration that no Conduit method reports, so nothing
        # offline can settle one.
        kind=FieldKind.INSTANCE_ENUM,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--icon",
        # project.search has no icon constraint: the search is run per icon
        # and the results merged, in `Project._search_by_look`.
        constraint=None,
        multiple=True,
        help="Projects shown with any of these icons.",
    ),
    Field(
        name="colors",
        kind=FieldKind.ENUM,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--color",
        # As `icons`: merged from one search per colour, never sent.
        constraint=None,
        choices=_PROJECT_COLORS,
        multiple=True,
        help="Projects shown in any of these colours.",
    ),
    Field(
        name="spaces",
        # Plural, and not the task search's `space`: that one takes a
        # comma-separated string of monograms, names and patterns, this one a
        # list. The two are kept apart rather than merged into a spelling
        # neither command has.
        kind=FieldKind.SPACE,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--space",
        constraint="spaces",
        multiple=True,
        help="Space monograms, names or patterns; none means the reader's default Space.",
    ),
    Field(
        name="show-members",
        kind=FieldKind.BOOL,
        objects=_PROJECT,
        verbs=_SEARCH,
        cli="--show-members",
        constraint=None,
        default=False,
        help="Display each project's members.",
    ),
    # --- passphrase, search ----------------------------------------------
    #
    # Two keys, and the walk behind them is the point: there is no
    # `passphrase.search`, only the legacy `passphrase.query`, which takes no
    # constraints at all. Both filters are applied in Python over every
    # credential the token can see - see docs/search-specs.md.
    Field(
        name="type",
        kind=FieldKind.TEXT,
        objects=_PASSPHRASE,
        verbs=_SEARCH,
        cli="--type",
        # passphrase.query has no constraints: applied in Python.
        constraint=None,
        help="Credential type: password, token, key or note.",
    ),
    # --- paste, search ----------------------------------------------------
    #
    # `text_query`, `author` and `limit` only, all declared above. The
    # command's other filters - --ids, --phids, --language, --status,
    # --created-after, --created-before, --order - are not spec keys yet:
    # `phabfive.search.dispatch._paste_params` does not build them, and a key
    # declared here that nothing reads is the drift this registry exists to
    # end. They are refused by name today, see
    # `phabfive.cli.search_spec.refuse_unspecced`.
    # --- project, create -------------------------------------------------
    #
    # The first two keys of a `projects:` item, declared in Phase 1 because
    # they are the two the validation layers could already say something
    # about. The rest of the key set is declared further down, with the task
    # and paste create blocks.
    Field(
        name="color",
        # Not INSTANCE_ENUM. The colour *keys* are fixed in Phorge's source -
        # projects.colors relabels a colour and can change the default, but
        # cannot add one - so a colour needs no network to check, and
        # `phabfive/project/core.py:check_project_color` already refuses an
        # unknown one offline. Layer 1 answers it, from `choices`.
        kind=FieldKind.ENUM,
        objects=_PROJECT,
        verbs=_CREATE,
        cli="--color",
        choices=_PROJECT_COLORS,
        help="The colour the project is shown in.",
    ),
    Field(
        name="icon",
        # The opposite case, and the reason INSTANCE_ENUM still exists here:
        # the icon set is `projects.icons` instance configuration and no
        # Conduit method reports it, so the icons projects carry are only an
        # approximation - the server still accepts a configured icon no
        # project uses yet. Layer 2 therefore *warns*, and never fails.
        kind=FieldKind.INSTANCE_ENUM,
        objects=_PROJECT,
        verbs=_CREATE,
        cli="--icon",
        help="The icon the project is shown with.",
    ),
    # --- task, create ----------------------------------------------------
    #
    # Exactly the keys `maniphest create` has a flag for. Four of them -
    # `status`, `column`, `visible-to` and `editable-by` - are what a spec
    # could not express before #481, and declaring them here is what makes
    # the two paths the same key set rather than two overlapping ones.
    #
    # `id:` and `tasks:` are **not** declared, and neither are `parent:`,
    # `parents:` and `subtasks:`: they are `references.STRUCTURAL_KEYS`. See
    # the note above DECLARED_COMPLETE.
    Field(
        name="title",
        kind=FieldKind.TEXT,
        # One declaration for a task and for a paste. Phorge's own API
        # agrees that they are the same key - the transaction is `title` on
        # `maniphest.edit` and on `paste.edit` alike - and so does the web
        # UI, which labels both "Name". Reading `fields.title` back out of
        # `paste.search` and `fields.name` out of `maniphest.search` is the
        # asymmetry, and it is on the read side, not here.
        objects=_TASK_PASTE,
        verbs=_CREATE,
        cli="--title",
        help="What it is called; the web UI labels this Name.",
    ),
    Field(
        name="description",
        kind=FieldKind.TEXT,
        objects=_TASK_PROJECT,
        verbs=_CREATE,
        cli="--description",
        help="The body text, in remarkup.",
    ),
    Field(
        name="priority",
        # The priority *names* are `maniphest.priority.map` instance
        # configuration, so nothing offline can list them - which is why
        # there are no `choices` here and why the same word is a
        # `FieldKind.PATTERN` on the search side, where it is a transition
        # grammar over a task's history rather than one value.
        kind=FieldKind.INSTANCE_ENUM,
        objects=_TASK,
        verbs=_CREATE,
        cli="--priority",
        help="Priority name, e.g. high or needs-triage.",
    ),
    Field(
        name="status",
        kind=FieldKind.INSTANCE_ENUM,
        objects=_TASK,
        verbs=_CREATE,
        cli="--status",
        help="Status key, e.g. open or resolved.",
    ),
    Field(
        name="assignment",
        kind=FieldKind.USER,
        objects=_TASK,
        verbs=_CREATE,
        # The spec key and the flag are spelled differently on purpose: the
        # template format has said `assignment:` since before there was a
        # registry, and renaming a key in a file people already have is a
        # change with no upside.
        cli="--assign",
        help="Assignee: a username, @me, or a user PHID.",
    ),
    Field(
        name="subscribers",
        kind=FieldKind.USER,
        objects=_TASK_PASTE,
        verbs=_CREATE,
        cli="--subscribe",
        multiple=True,
        help="Subscribers: usernames, @me, or user PHIDs.",
    ),
    Field(
        name="projects",
        kind=FieldKind.PROJECT,
        objects=_TASK_PASTE,
        verbs=_CREATE,
        cli="--tag",
        multiple=True,
        help="Projects to tag it into, by name, #hashtag or PHID.",
    ),
    Field(
        name="column",
        # A board's columns are instance data - they are created per
        # workboard - so nothing offline can list them, and a column only
        # means something together with the board it is on: a spec writing
        # `column:` without `projects:` is refused the way
        # `phabfive.edit.validators.validate_board_column_context` refuses
        # `--column` without `--tag`. `phabfive.spec.validate` is where that
        # rule lives and `phabfive.spec.create._columns` is what resolves
        # the name against the board.
        #
        # A spec places the task in one `maniphest.edit`, where `maniphest
        # create --column` creates the task and then sends a second edit
        # carrying `objectIdentifier`. The one-edit form is verified against
        # a real Phorge; a create spec has no code path that may carry an
        # `objectIdentifier` at all, which is what makes anchoring to an
        # existing object safe.
        kind=FieldKind.INSTANCE_ENUM,
        objects=_TASK,
        verbs=_CREATE,
        cli="--column",
        help="Workboard column to place it in; needs projects: as well.",
    ),
    Field(
        name="space",
        kind=FieldKind.SPACE,
        objects=_TASK_PROJECT,
        verbs=_CREATE,
        cli="--space",
        # One Space, not a filter over several: the search-side `space` and
        # `spaces` keys take patterns and mean "any of these", while this
        # one names the single Space the object is created in.
        help="The Space to create it in, by name or monogram.",
    ),
    Field(
        name="visible-to",
        kind=FieldKind.POLICY,
        objects=_CREATED,
        verbs=_CREATE,
        cli="--visible-to",
        help="View policy: a keyword, #project, @user or PHID.",
    ),
    Field(
        name="editable-by",
        kind=FieldKind.POLICY,
        objects=_CREATED,
        verbs=_CREATE,
        cli="--editable-by",
        help="Edit policy: a keyword, #project, @user or PHID.",
    ),
    # --- project, create, continued --------------------------------------
    #
    # `color:` and `icon:` are declared above, with the note about why a
    # colour needs no network and an icon does.
    Field(
        name="name",
        kind=FieldKind.TEXT,
        objects=_PROJECT,
        verbs=_CREATE,
        # A positional argument on `project create`, not an option, the way
        # `text_query` is on `maniphest search`.
        cli=None,
        help="The project's name, which is what its hashtag is derived from.",
    ),
    Field(
        name="slugs",
        kind=FieldKind.TEXT,
        objects=_PROJECT,
        verbs=_CREATE,
        cli="--slug",
        multiple=True,
        # `slugs` is the one collection transaction in phabfive with no
        # `.add` spelling: `project.edit` replaces the whole list. That is
        # safe only on an object being created, which is why a create spec
        # is the only place this key may appear at all.
        help="Additional hashtags, with or without their '#'.",
    ),
    Field(
        name="members",
        kind=FieldKind.USER,
        objects=_PROJECT,
        verbs=_CREATE,
        cli="--member",
        multiple=True,
        help="Members: usernames, @me, or user PHIDs.",
    ),
    Field(
        name="parent",
        # A project, not a monogram: the same word on the search side names
        # a task's parent by `T123`, which is a different question.
        kind=FieldKind.PROJECT,
        objects=_PROJECT,
        verbs=_CREATE,
        cli="--parent",
        help="Create it as a subproject of this project.",
    ),
    Field(
        name="milestone-of",
        kind=FieldKind.PROJECT,
        objects=_PROJECT,
        verbs=_CREATE,
        cli="--milestone-of",
        # A milestone takes no icon, colour or hashtag: its icon is fixed,
        # its colour is its parent's and it has no slug. `project create`
        # refuses the combination and a spec has to be refused the same way.
        help="Create it as a milestone of this project; takes no icon or slugs.",
    ),
    Field(
        name="joinable-by",
        kind=FieldKind.POLICY,
        objects=_PROJECT,
        verbs=_CREATE,
        cli="--joinable-by",
        help="Join policy: a keyword, #project, @user or PHID.",
    ),
    # --- paste, create ---------------------------------------------------
    #
    # `title`, `projects`, `subscribers`, `visible-to` and `editable-by` are
    # declared above, shared with a task. These two are the paste's own.
    Field(
        name="content",
        kind=FieldKind.TEXT,
        objects=_PASTE,
        verbs=_CREATE,
        cli="--content",
        help="The paste's text.",
    ),
    Field(
        name="language",
        kind=FieldKind.TEXT,
        objects=_PASTE,
        verbs=_CREATE,
        cli="--language",
        help="Language for syntax highlighting, e.g. python.",
    ),
)


#: The ``(object type, verb)`` pairs whose declared key set is **complete**,
#: and therefore the only ones an undeclared key is reported as
#: ``unknown-key`` for.
#:
#: Without this, declaring the first field of an object type would make every
#: *other* key of that object an error: `validate._check_fields` reports an
#: unknown key as soon as the pair has any declared field at all. A pair
#: joins this set in the change that finishes its key set - and
#: `docs/phorge-spec.md` has to say so in the same change, which
#: `tests/test_spec_docs.py` is what holds it to.
#:
#: **A create pair's key set is more than its fields.** `id:`, `tasks:`,
#: `parent:`, `parents:` and `subtasks:` are structure rather than values - a
#: local name, a list of child items, and references that may be a
#: `$local-id` - so they are not `Field`s, and declaring `parents:` as a
#: `FieldKind.MONOGRAM` would make the offline pass call `$epic` a
#: `bad-monogram`. `phabfive.spec.references.STRUCTURAL_KEYS` lists them, and
#: `validate._check_fields` and `schema._create_item_schema` both read it, so
#: the walk and the oracle agree about a create item's whole key set.
#:
#: Until #518 the create pairs were left out of this set for exactly that
#: reason, and a task written with `tags:` instead of `projects:` validated
#: clean and was created attached to nothing.
DECLARED_COMPLETE: frozenset[tuple[str, str]] = frozenset(
    {
        ("task", "search"),
        ("project", "search"),
        ("paste", "search"),
        ("passphrase", "search"),
        ("task", "create"),
        ("project", "create"),
        ("paste", "create"),
    }
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
