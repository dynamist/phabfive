# -*- coding: utf-8 -*-
"""Planning a search from a spec, as data, before anything is queried.

A search is two steps with an inspectable object between them, the shape
`phabfive/edit/` already proves for an edit::

    search spec (Spec)  --plan-->  SearchPlan  --run-->  SearchResult

:func:`plan_search` is the first step and :func:`run_search` the second, and
neither prints, prompts, exits or reads a terminal. Everything that used to
live inside ``phabfive.cli.maniphest.search`` and could not be reached from a
program lives here: the precedence between what a caller supplied and what
the spec holds, the transition patterns, the task-id grammar of
``include:``/``exclude:``, and whether the search has any criteria at all.

What a program does with it::

    from phabfive import Maniphest
    from phabfive.spec import load_spec
    from phabfive.spec.search import plan_searches, run_search

    app = Maniphest(url=URL, token=TOKEN)        # explicit; discovers nothing
    spec = load_spec("mine.yaml", kind="search")

    for plan in plan_searches(app, spec):        # every reference resolved once
        result = run_search(app, plan)
        for task in result.records:
            ...

There is deliberately no batch container. `phabfive.edit.plan.EditPlan` exists
to carry per-task failures and per-task confirmations; a search has neither -
a reference that does not resolve is reported atomically before anything runs,
and nothing is confirmed - so :func:`plan_searches` answers with a plain list.

Two entry points, and the difference matters:

- :func:`plan_search` plans **one** item. The command uses this, in a loop,
  because a template's second search must not be validated before the first
  one's results have been printed: today a bad pattern in document 2 raises
  after document 1 has already searched and printed, and that ordering is
  part of what the command promises.
- :func:`plan_searches` plans **every** item, after one atomic online
  validation pass, which is what "a search naming three projects that do not
  exist should say so once" needs. A library caller wanting the whole report
  up front calls this one.

Library code: nothing here prints, prompts, exits, reads the environment or
reads a configuration file, and nothing here imports `phabfive.cli`. The app
is handed in already constructed, exactly as `phabfive.spec.online` takes one.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional, Protocol

from phabfive.exceptions import PhabfiveDataException, PhabfiveInputException
from phabfive.spec.envelope import DATA_SOURCE, DEFAULT_SEARCH_TYPE
from phabfive.spec.problems import Severity
from phabfive.spec.registry import OBJECT_TYPES, spec_keys

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from phabfive.spec.envelope import Spec

__all__ = [
    "CRITERIA_PARAMS",
    "PARAM_DEFAULTS",
    "SEARCH_PARAMS",
    "SearchApp",
    "SearchPlan",
    "SearchPlanError",
    "SearchResult",
    "banner_title",
    "check_search_params",
    "plan_search",
    "plan_searches",
    "run_search",
    "task_ids",
    "wants_banner",
]


#: The object types **this module** plans, which is the one whose parameters
#: are `Maniphest.task_search`'s. The other three are planned and run by
#: `phabfive.search.dispatch`, which knows the other apps; this layer may not
#: import one. So a `type:` outside this set is refused here rather than
#: quietly planned as a task search, and the caller says which command runs
#: it.
_RUNNABLE_TYPES = frozenset({"task"})

#: Spec key -> the `Maniphest.task_search` keyword it becomes, for every value
#: that is passed through as written. A spec spells its keys with hyphens;
#: `text_query` is the one key spelled with an underscore in both, and the
#: registry is what says so. Do not "fix" either spelling here.
SEARCH_PARAMS: Mapping[str, str] = {
    "text_query": "text_query",
    "tag": "tag",
    "assigned": "assigned",
    "author": "author",
    "space": "space",
    "visible-to": "visible_to",
    "editable-by": "editable_by",
    "created-after": "created_after",
    "created-before": "created_before",
    "updated-after": "updated_after",
    "updated-before": "updated_before",
    # The constraints maniphest.search always answered and phabfive did not
    # send (#478). Each one is declared once in `phabfive.spec.registry`;
    # this table is only how a spec key reaches the keyword `task_search`
    # spells it, which is the same passing through as the keys above.
    "ids": "ids",
    "phids": "phids",
    "subscriber": "subscriber",
    "subtype": "subtype",
    "parent": "parent",
    "subtask": "subtask",
    "has-parents": "has_parents",
    "has-subtasks": "has_subtasks",
    "closed-by": "closed_by",
    "closed-after": "closed_after",
    "closed-before": "closed_before",
}

#: What a key means when neither the caller nor the spec supplies it. These
#: are the *command's* defaults rather than the registry's documented ones:
#: `status` documents "open" because that is the scope an unfiltered search
#: reaches, but the absence of a status pattern is what produces it, and
#: sending `status: open` as a pattern would not be the same search.
PARAM_DEFAULTS: Mapping[str, Any] = {
    "show-history": False,
    "show-metadata": False,
    "show-policy": False,
    "all": False,
    "limit": 100,
}

#: The `params` entries whose value decides that a search was asked for at
#: all. An unconstrained search reads the whole instance, so a spec that names
#: none of these is a mistake rather than a request. `exclude_task_ids` is not
#: here on purpose - removing tasks from nothing is still nothing - and
#: neither are the `show_*` keys, which change the display and not the query.
#: Named as `Maniphest.task_search` names them, because that is how
#: `SearchPlan.params` is keyed.
CRITERIA_PARAMS: tuple[str, ...] = (
    "text_query",
    "tag",
    "assigned",
    "author",
    "space",
    "visible_to",
    "editable_by",
    "created_after",
    "created_before",
    "updated_after",
    "updated_before",
    "include_closed",
    "column_patterns",
    "priority_patterns",
    "status_patterns",
    "include_task_ids",
    # The server-side constraints of #478. `has_parents`/`has_subtasks` are
    # listed here as well as in `_TRISTATE_PARAMS`, which is what makes their
    # `False` half count: this is a truthiness test and `has-parents: false`
    # - tasks with no parent, a real question - is tested for presence there.
    "ids",
    "phids",
    "subscriber",
    "subtype",
    "parent",
    "subtask",
    "has_parents",
    "has_subtasks",
    "closed_by",
    "closed_after",
    "closed_before",
)


#: The `params` entries that are **tri-state**: absent, true or false, where
#: false is a filter and not the absence of one. Tested for presence rather
#: than for truth by `SearchPlan.has_criteria`.
_TRISTATE_PARAMS: tuple[str, ...] = ("has_parents", "has_subtasks")


class SearchPlanError(PhabfiveInputException):
    """A search that cannot be planned, and which check refused it.

    `check` is a stable name for the check that refused it -
    "invalid-pattern", "invalid-task-id", "include-exclude-overlap",
    "unsupported-type" - so a caller can answer each one differently without
    matching on the message. A frontend renders it against the field; the
    command uses it to keep each message's wording exactly as it has always
    been printed.

    Spelled `check` rather than `code` because under `phabfive/spec/` a
    `code=` is a `phabfive.spec.problems.Problem` slug, and
    `tests/test_spec_problems.py` holds that vocabulary to the documented
    list. These name a raise rather than a reported problem, and the two
    vocabularies are kept apart.

    `object_type` is carried for the one check that is about a type rather
    than a value - "unsupported-type" - so a command can name the command
    that *does* run that type without reading the sentence back apart.

    A `PhabfiveInputException`, so every handler that already answers "that
    argument value is not one phabfive can use" answers this too.
    """

    def __init__(
        self, message: str, *, check: str, object_type: Optional[str] = None
    ) -> None:
        super().__init__(message)
        self.check = check
        self.object_type = object_type


class SearchApp(Protocol):
    """As much of an app as planning and running a search needs.

    Deliberately narrower than `phabfive.core.Phabfive`: a plan asks the
    instance exactly one thing - whether a status pattern names a status it
    knows - and a run calls one search method. Saying so as a protocol is
    what lets a caller hand in something else, and what keeps this module
    from importing `phabfive.maniphest` and dragging an app into the spec
    layer.

    `Maniphest` satisfies it. The methods widen as the other object types
    arrive; until then the only runnable `type:` is "task".
    """

    def parse_status_patterns_with_api(self, value: Any) -> Any:
        """The status transition grammar, checked against this instance."""
        ...  # pragma: no cover - protocol

    def task_search(self, **params: Any) -> Any:
        """One task search, taking `SearchPlan.params` as keywords."""
        ...  # pragma: no cover - protocol


@dataclasses.dataclass(frozen=True)
class SearchPlan:
    """One validated, resolved search, ready to run.

    Attributes
    ----------
    object_type : str
        What is searched: "task" today, the rest of
        `phabfive.spec.registry.OBJECT_TYPES` as their apps arrive.
    params : mapping
        Exactly the keyword arguments the app's search method takes, already
        interpreted: parsed pattern objects, task ids as ints, and every
        other value as the string the method accepts. This is the contract
        the port is checked against, so nothing may repurpose it - a later
        issue adding raw Conduit constraints adds an attribute beside it.
    title : str or None
        This search's banner title, as the spec wrote it. **Not** the file's
        `metadata: name:` - one describes a result, the other the document.
    description : str or None
        This search's banner description, likewise.
    index : int
        1-based position among the spec's searches.
    total : int
        How many searches the spec holds, which is what decides whether a
        single unnamed search needs a banner to be told apart from anything.
    source : str
        Where the spec came from, for messages only. Not part of equality:
        the same search read from a file and from a dict is the same search.
    records_key : str or None
        Which key of the answer holds the records, when the search method
        answers with a mapping. ``"tasks"`` for a task search; the other
        object types are set from `phabfive.search.dispatch`, which is
        where an endpoint's answer shape is declared. ``None`` means the
        method answers with the records themselves.

    Not hashable. `params` is a mapping, so a generated ``__hash__`` would
    exist and raise `TypeError: unhashable type: 'dict'` the first time
    somebody put a plan in a set - a promise the class cannot keep. Saying
    so up front makes the failure name the plan instead.
    """

    object_type: str = DEFAULT_SEARCH_TYPE
    params: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    title: Optional[str] = None
    description: Optional[str] = None
    index: int = 1
    total: int = 1
    source: str = dataclasses.field(default=DATA_SOURCE, compare=False)
    records_key: Optional[str] = dataclasses.field(default="tasks", compare=False)

    __hash__ = None  # type: ignore[assignment]

    @property
    def conflicting_status_scopes(self) -> tuple[str, ...]:
        """The status scopes this search's deprecated `all:` contradicts.

        `all:` is the older spelling of a status scope of "any", so naming it
        beside a `status:` group that says "open" or "closed" asks for two
        different scopes at once. Empty when there is no contradiction, which
        includes every search that does not use `all:`.

        Reported rather than raised: a caller decides what a deprecated key
        colliding with a current one is worth, and the command makes it an
        error after it has warned about the deprecation.
        """
        if not self.params.get("include_closed"):
            return ()

        named = {
            condition.get("type")
            for pattern in self.params.get("status_patterns") or []
            for condition in pattern.conditions
        } & {"open", "closed"}

        return tuple(sorted(named))

    @property
    def has_criteria(self) -> bool:
        """Whether anything was asked for.

        False means the plan would read the whole instance, which every
        caller refuses rather than runs. A status scope of "any" counts as a
        criterion, because that is how a script asks for every task on
        purpose, and so does the deprecated `all:`, the older spelling of the
        same request.

        `has_parents` and `has_subtasks` are tri-state, so they are tested
        for *presence* and not for truth: ``has-parents: false`` is "tasks
        with no parent at all", which is as real a filter as its opposite and
        used to print the command's usage instead of running.
        """
        if any(self.params.get(key) is not None for key in _TRISTATE_PARAMS):
            return True

        return any(self.params.get(key) for key in CRITERIA_PARAMS)

    @property
    def wants_banner(self) -> bool:
        """Whether this search's results need a label to be told apart.

        See :func:`wants_banner`, which this is the bound form of.
        """
        return wants_banner(self.title, self.description, self.total)

    @property
    def banner_title(self) -> str:
        """The title to label this search's results with.

        See :func:`banner_title`, which this is the bound form of.
        """
        return banner_title(self.title, self.index)

    @property
    def limit(self) -> Any:
        return self.params.get("limit")

    @property
    def order(self) -> Any:
        return self.params.get("order")

    @property
    def text_query(self) -> Any:
        return self.params.get("text_query")


@dataclasses.dataclass(frozen=True)
class SearchResult:
    """What one plan answered with.

    Attributes
    ----------
    plan : SearchPlan
        The plan that was run, so a result carries its own question.
    payload : any
        Exactly what the app's search method returned, untranslated. For a
        task search that is the whole display structure - the tasks, the
        transition maps, the matched-board maps and the parameters that were
        sent - and it is `None` when nothing matched a project filter, which
        is not an error. Translating it here would lose everything the
        display consumes.
    """

    plan: SearchPlan
    payload: Any = None

    __hash__ = None  # type: ignore[assignment]

    @property
    def records(self) -> list:
        """The objects found, as a plain list, whatever the payload holds.

        The uniform view over every object type: the plan says which key of
        a mapping payload holds the records - `"tasks"` for a task search,
        `"projects"` and `"pastes"` for the two that answer with a mapping
        of their own - the payload itself when an app answers with a list,
        and `[]` when there was no answer at all.

        `phabfive.search.records_of` is the same answer, and is what a
        caller holding only the runner table reaches for.
        """
        if self.payload is None:
            return []

        if isinstance(self.payload, Mapping):
            if self.plan.records_key is None:
                return []

            return list(self.payload.get(self.plan.records_key) or [])

        if isinstance(self.payload, Sequence) and not isinstance(
            self.payload, (str, bytes, bytearray)
        ):
            return list(self.payload)

        return [self.payload]


def wants_banner(title: Optional[str], description: Optional[str], total: int) -> bool:
    """Whether one search's results need a label to be told apart.

    A named search always does; an unnamed one does only when it is not the
    only search in the spec. Whether a banner is *printed* is the caller's
    decision - a machine format never prints one - and this only says whether
    there is anything to print.

    A function as well as a `SearchPlan` property because the command prints
    the banner *before* it plans, so that a search whose pattern does not
    parse still says which search it was.
    """
    return total > 1 or bool(title) or bool(description)


def banner_title(title: Optional[str], index: int) -> str:
    """The title to label one search's results with.

    The spec's title, or "Search 3" when its author did not name it.
    """
    return title or f"Search {index}"


def check_search_params(
    params: Any,
    *,
    where: str,
    object_type: str = DEFAULT_SEARCH_TYPE,
    keys: Optional[Iterable[str]] = None,
) -> None:
    """Refuse a `search:` section that is not one, or a key no `Field` declares.

    A key that is accepted and then ignored is the drift the registry exists
    to end (#295), so a spec naming one is refused by name instead.

    `spec_keys` is called here rather than read from a set built at import,
    which is what lets a newly declared field be accepted with no import
    order to get right.

    One check for every object type, so the four cannot drift on the
    sentence: `phabfive.search.dispatch` passes its runner's interim key set
    through `keys` for the types the registry has not finished declaring,
    and passes nothing once it has.

    Parameters
    ----------
    params : mapping
        One search's parameters, as the spec wrote them. An empty mapping is
        a search with no constraints, which is a real thing to write; `None`
        - what an empty `search:` parses to - is not, and is refused.
    where : str
        What to call this search in the message, e.g. "Document 2".
    object_type : str
        Whose keys to check against, when `keys` is not given.
    keys : iterable of str, optional
        The accepted key set, in place of the registry's. For a caller that
        has one the registry does not declare yet.

    Raises
    ------
    PhabfiveDataException
        The section is not a mapping, or the spec names a key nothing reads.
    """
    if not isinstance(params, Mapping):
        raise PhabfiveDataException(f"{where}: 'search' section must be a dictionary")

    supported = (
        frozenset(keys) if keys is not None else spec_keys(object_type, "search")
    )
    unsupported = set(params) - supported

    if unsupported:
        raise PhabfiveDataException(
            f"{where}: Unsupported search parameters: {', '.join(sorted(unsupported))}. "
            f"Supported: {', '.join(sorted(supported))}"
        )


def _value(
    params: Mapping[str, Any],
    overrides: Mapping[str, Any],
    key: str,
    default: Any = None,
) -> Any:
    """One key's value: the override, else the spec's, else the default.

    An override that is `None` counts as absent. That is what makes the
    command's sentinels work: typer has no "unset" marker, so a flag that was
    not given arrives as `None` and must not beat the spec's value.
    """
    supplied = overrides.get(key)

    if supplied is not None:
        return supplied

    return params.get(key, default)


def _patterns(raw: Any, label: str, parse) -> Any:
    """One transition filter, parsed, or None when there is none.

    `parse` is caught broadly on purpose: the grammar's own errors are
    `PhabfiveInputException`, but a pattern naming a status only the instance
    knows is parsed against the instance, and whatever that raises is still
    "this pattern is not usable" rather than a crash.
    """
    if not raw:
        return None

    try:
        return parse(raw)
    except Exception as error:
        raise SearchPlanError(
            f"Invalid {label} filter pattern: {error}", check="invalid-pattern"
        ) from error


def task_ids(value: Any, *, option: Optional[str] = None) -> Optional[list]:
    """Task monograms as ints, from a string, a list, or a list of strings.

    Accepts "T123,T456", ["T123", "T456"], and ["T123,T456", "T789"] - the
    last because a spec's list entry may itself hold a comma and refusing it
    would be a distinction without a difference. Anything that is not a task
    monogram is refused by name; another application's monogram is not a
    task, which is why the grammar is anchored to maniphest's alone, and a
    bare number is not one either - ``T123`` is what the registry declares
    (`monograms=("T",)`), what the offline pass checks a spec's value
    against, and what every one of these keys says in its help.

    **One grammar for every key that takes a task id.** `include:` and
    `exclude:` are read here and `ids:`, `parent:` and `subtask:` in
    `phabfive.maniphest.core`, and the two used to disagree: ``--ids 307``
    was accepted while ``--include 307`` was refused, for the same kind of
    value on the same command.

    Parameters
    ----------
    value
        What was written: a string, a number, or a list of either.
    option : str, optional
        The flag to name in the error, e.g. ``"--ids"``. Left out for the
        keys whose message has never carried one.

    Returns
    -------
    list or None
        The ids as ints, or None when nothing usable was written.

    Raises
    ------
    SearchPlanError
        An entry that is not a task monogram. `check` is "invalid-task-id".
        A `PhabfiveInputException`, so a caller that only knows that type
        still catches it.
    """
    if not value:
        return None

    from phabfive.constants import MONOGRAMS

    entries: Iterable[Any] = value if isinstance(value, (list, tuple)) else [value]
    grammar = re.compile(f"^{MONOGRAMS['maniphest']}$")
    where = f" for {option}" if option else ""

    found = []
    for entry in entries:
        for part in str(entry).split(","):
            part = part.strip()

            if not part:
                continue

            if not grammar.match(part):
                raise SearchPlanError(
                    f"Invalid task ID '{part}'{where}. Expected format: T123",
                    check="invalid-task-id",
                )

            found.append(int(part[1:]))

    return found or None


def plan_search(
    app: SearchApp,
    item: Mapping[str, Any],
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    index: int = 1,
    total: int = 1,
    source: str = DATA_SOURCE,
) -> SearchPlan:
    """Interpret one `searches:` item into a runnable plan.

    Parameters
    ----------
    app : SearchApp
        An already-constructed app, e.g. a `Maniphest`. Used for the one
        piece of interpretation that needs the instance - a status pattern
        may name a status only this Phorge knows - and for nothing else.
        Nothing is constructed here and no configuration is read.
    item : mapping
        One item as `Spec.items("search")` yields it: `type:`, `search:`, and
        this search's banner `title:`/`description:`. A bare parameter
        mapping is not one - wrap it as ``{"search": params}``.
    overrides : mapping, optional
        Values that beat the spec's, keyed exactly as a spec key is spelled.
        A key whose value is `None` counts as not supplied, which is what
        lets a command pass a flag that was never given.
    index, total : int
        This search's 1-based position and how many the spec holds, which is
        what `SearchPlan.wants_banner` reads.
    source : str
        Where the spec came from, for messages.

    Raises
    ------
    SearchPlanError
        A pattern that does not parse, a task id that is not one, an id in
        both `include:` and `exclude:`, or a `type:` that cannot be run.
    PhabfiveDataException
        The item names a search key nothing reads.
    """
    overrides = overrides or {}
    params: Mapping[str, Any] = item.get("search", {})
    object_type = item.get("type") or DEFAULT_SEARCH_TYPE

    if object_type not in _RUNNABLE_TYPES:
        known = ", ".join(sorted(OBJECT_TYPES))
        raise SearchPlanError(
            f"This plans a task search, not a {object_type!r} one. A search "
            f"names one of: {known}; use phabfive.search.plan_item for the "
            "other three",
            check="unsupported-type",
            object_type=object_type,
        )

    check_search_params(params, where=f"searches[{index - 1}]")

    # The order of what follows is load-bearing: it is the order a spec with
    # several mistakes reports them in, which is the order the command has
    # always reported them in.
    from phabfive.transitions import parse_column_patterns, parse_priority_patterns

    column_patterns = _patterns(
        _value(params, overrides, "column"), "column", parse_column_patterns
    )
    priority_patterns = _patterns(
        _value(params, overrides, "priority"), "priority", parse_priority_patterns
    )
    status_patterns = _patterns(
        _value(params, overrides, "status"),
        "status",
        app.parse_status_patterns_with_api,
    )

    include_task_ids = task_ids(_value(params, overrides, "include"))
    exclude_task_ids = task_ids(_value(params, overrides, "exclude"))

    overlap = set(include_task_ids or []) & set(exclude_task_ids or [])
    if overlap:
        named = ", ".join(f"T{task_id}" for task_id in sorted(overlap))
        raise SearchPlanError(
            f"{named} cannot be both included and excluded",
            check="include-exclude-overlap",
        )

    resolved: dict[str, Any] = {
        parameter: _value(params, overrides, key)
        for key, parameter in SEARCH_PARAMS.items()
    }
    resolved.update(
        {
            "include_task_ids": include_task_ids,
            "exclude_task_ids": exclude_task_ids,
            "column_patterns": column_patterns,
            "priority_patterns": priority_patterns,
            "status_patterns": status_patterns,
            "show_history": _value(
                params, overrides, "show-history", PARAM_DEFAULTS["show-history"]
            ),
            "show_metadata": _value(
                params, overrides, "show-metadata", PARAM_DEFAULTS["show-metadata"]
            ),
            "show_policy": _value(
                params, overrides, "show-policy", PARAM_DEFAULTS["show-policy"]
            ),
            "include_closed": _value(params, overrides, "all", PARAM_DEFAULTS["all"]),
            "limit": _value(params, overrides, "limit", PARAM_DEFAULTS["limit"]),
            # Left possibly None so the app applies its own default; a
            # non-None default here would silently beat a spec's `order:`.
            "order": _value(params, overrides, "order"),
        }
    )

    return SearchPlan(
        object_type=object_type,
        params=resolved,
        title=item.get("title"),
        description=item.get("description"),
        index=index,
        total=total,
        source=source,
    )


def plan_searches(
    app: SearchApp,
    spec: "Spec",
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    validate: bool = True,
) -> list[SearchPlan]:
    """Every search a spec holds, validated up front.

    The atomic form: the whole spec's references are resolved against the
    instance in one pass before the first plan is built, so a spec naming
    three projects that do not exist says so once instead of failing partway
    through the second search's paging.

    The command deliberately does **not** use this. Its searches are printed
    one after another, and moving every check ahead of the first one's output
    would change the order a person sees results and errors in. A program
    that wants the whole report at once is exactly who this is for.

    How much a search spec's online pass answers is
    `phabfive.spec.references.REFERENCE_FIELDS`'s to say: which keys of a
    ``searches:`` item hold a reference is declared there. Today that is the
    four user filters - ``assigned:``, ``author:``, ``subscriber:`` and
    ``closed-by:`` - each of which is already a hard failure when it names
    somebody the instance does not have, so resolving them here changes when
    that is reported and not whether. ``tag:`` and ``space:`` are
    deliberately not declared: a project or a Space that matches nothing is
    a log line and exit 0 today, so declaring either would change what every
    existing template does. Adding one there starts it being resolved before
    the first query with no change here.

    Parameters
    ----------
    app : SearchApp
        An already-constructed app, e.g. a `Maniphest`.
    spec : Spec
        A search spec. Render it first if it declares variables: a value
        still holding ``{{ who }}`` names nothing an instance could answer
        for.
    overrides : mapping, optional
        Values that beat the spec's, applied to every item.
    validate : bool
        Whether to ask the instance about the spec's references first. False
        skips the requests, for a caller that has already validated.

    Raises
    ------
    PhabfiveDataException
        The instance answered "no such thing" for at least one reference.
        Every one of them is named, in document order.
    SearchPlanError
        An item cannot be planned. The first one refuses the whole spec,
        because a plan list with a hole in it is worse than an error.
    """
    if validate:
        # Only the errors stop a search. A warning - an icon outside the set
        # this instance has been seen to use, say - is something to report,
        # not a reason to refuse to search.
        problems = [
            one for one in spec.validate_online(app) if one.severity == Severity.ERROR
        ]

        if problems:
            raise PhabfiveDataException(
                "\n".join(
                    [f"{len(problems)} reference(s) could not be resolved:"]
                    + [
                        "  - "
                        + (f"{one.object}.{one.field}" if one.field else one.object)
                        + f": {one.reason}"
                        for one in problems
                    ]
                )
            )

    items = spec.items("search")

    return [
        plan_search(
            app,
            item,
            overrides=overrides,
            index=index,
            total=len(items),
            source=spec.source,
        )
        for index, item in enumerate(items, start=1)
    ]


def run_search(app: SearchApp, plan: SearchPlan) -> SearchResult:
    """Run one plan and return what the app answered.

    The payload is not translated: a task search answers with the whole
    structure the display consumes, and `None` when a project filter matched
    nothing - which is an empty result, not an error.

    Parameters
    ----------
    app : SearchApp
        The app for this plan's object type, already constructed.
    plan : SearchPlan

    Raises
    ------
    PhabfiveConfigException, PhabfiveDataException
        The app refused the search: a policy value outside the grammar, or a
        project or user it names that does not exist. Both are found before
        anything is fetched.
    """
    if plan.object_type not in _RUNNABLE_TYPES:  # pragma: no cover - plan refuses first
        raise SearchPlanError(
            f"This runs a task search, not a {plan.object_type!r} one",
            check="unsupported-type",
            object_type=plan.object_type,
        )

    return SearchResult(plan=plan, payload=app.task_search(**plan.params))
