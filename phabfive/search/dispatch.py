# -*- coding: utf-8 -*-
"""What a `type:` searches, and the one call that runs any of them.

A search spec names its target per item::

    kind: search
    searches:
      - type: task
        search: {column: "in:Blocked"}
      - type: project
        search: {status: active, members: ["@me"]}
      - type: paste
        search: {author: "@me"}
      - type: passphrase
        search: {type: key}

`type:` defaults to ``task``, which is what every search template in the tree
means today, so a template written before this existed keeps working.

The table below is the whole dispatch: one :class:`SearchRunner` per object
type, saying which app class runs it, which method, which keys its ``search:``
section may hold, and how those keys become that method's keyword arguments.
The app classes are named as strings and imported at call time - the
``_LAZY`` pattern `phabfive/__init__.py` already uses - so planning a task
search never imports `Passphrase`.

Why this is not in `phabfive/spec/`: running a search needs the apps, and
`phabfive.spec` must import none of them. Why it is not in one app package:
one call has to cover four object types, and hanging that off `Maniphest`
would make a paste search a maniphest feature. Nothing here prints, prompts,
exits, reads the environment or reads a configuration file; the command is
`phabfive.cli.search_spec`.

The odd one out is **passphrase**. There is no ``passphrase.search``, only
the legacy ``passphrase.query``, which takes no constraints at all: both the
name and the type filter are applied in Python, and ``limit`` is counted
locally. So a passphrase search costs a walk of every credential the token can
see, and a small ``limit`` only shortens the walk once enough have matched.
That is documented rather than hidden - see ``docs/search-templates.md`` - and
it is why a spec cannot ask a passphrase search for secret material: the walk
would fetch the secret of every credential on the instance, not of the ones
that matched.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from importlib import import_module
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Optional

from phabfive.exceptions import PhabfiveDataException, PhabfiveInputException
from phabfive.spec.envelope import DATA_SOURCE, DEFAULT_SEARCH_TYPE
from phabfive.spec.registry import (
    OBJECT_TYPES,
    constraint_for,
    field_by_name,
    spec_keys,
)
from phabfive.spec.search import (
    PARAM_DEFAULTS,
    SearchPlan,
    SearchPlanError,
    SearchResult,
    check_search_params,
    plan_search,
    run_search,
)

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from phabfive.spec.envelope import Spec

__all__ = [
    "SEARCH_RUNNERS",
    "SearchRunner",
    "accepted_keys",
    "app_for",
    "has_criteria",
    "plan_item",
    "records_of",
    "run_item",
    "run_spec",
    "runner_for",
    "searched_text",
]


#: What a builder is handed: the app, already constructed, and this search's
#: values keyed exactly as a spec spells them, with the caller's overrides
#: already applied. It answers with the search method's keyword arguments.
ParamsBuilder = Callable[[Any, Mapping[str, Any]], "dict[str, Any]"]


@dataclasses.dataclass(frozen=True)
class SearchRunner:
    """How one object type is searched.

    Attributes
    ----------
    object_type : str
        The `type:` a spec writes, one of
        `phabfive.spec.registry.OBJECT_TYPES`.
    module, app : str
        The module and class of the app that runs it, imported at call time.
    method : str
        The method on that app which runs one search.
    keys : tuple of str
        The keys this object type's `search:` section may hold, until the
        registry declares them - see :func:`accepted_keys`.
    build : callable or None
        Turns those values into the method's keyword arguments. ``None``
        means `phabfive.spec.search.plan_search` plans this type, which is
        the task search's whole interpretation and is not restated here.
    records_key : str or None
        The key of the payload mapping that holds the records found. ``None``
        when the method answers with the records themselves.
    criteria : tuple of str
        The *parameter* names whose value decides that something was asked
        for. Empty means a search with no filters is a real request:
        `project search` with none lists every active project, while a paste
        or passphrase search with none reads the whole instance.
    text_key : tuple of str
        The path through the parameters to the free text this search matches
        on, for a caller that wants to say why nothing matched.
    """

    object_type: str
    module: str
    app: str
    method: str
    keys: tuple[str, ...] = ()
    build: Optional[ParamsBuilder] = None
    records_key: Optional[str] = None
    criteria: tuple[str, ...] = ()
    text_key: tuple[str, ...] = ()


def _as_list(value: Any) -> list[str]:
    """A repeatable filter's value, as a list, however the spec wrote it.

    ``members: "@me"``, ``members: "@me,alice"`` and ``members: ["@me",
    "alice"]`` are the same three spellings the CLI's list options accept,
    read the same way - `phabfive.options.split_list_option` is the rule.
    """
    from phabfive.options import split_list_option

    if value is None:
        return []

    if isinstance(value, (list, tuple)):
        return split_list_option([str(entry) for entry in value])

    return split_list_option(str(value))


def _as_bool(value: Any, key: str) -> Optional[bool]:
    """A tri-state flag: True, False, or absent.

    Absent is not False - ``milestones:`` unset means "both", which is
    neither "only milestones" nor "no milestones".
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    raise PhabfiveInputException(
        f"{key}: is true or false, not {value!r}",
    )


def _limit(value: Any) -> Optional[int]:
    """How many records to return: ``0`` means every match, as everywhere.

    Absent is the command's default of 100 rather than "every match", which
    is what `maniphest search`, `project search`, `paste search` and
    `passphrase search` all do with no `--limit`.
    """
    if value is None:
        value = PARAM_DEFAULTS["limit"]

    try:
        limit = int(value)
    except (TypeError, ValueError) as error:
        raise PhabfiveInputException(
            f"limit: is a number of records, 0 for all, not {value!r}"
        ) from error

    return limit if limit > 0 else None


def _project_params(app: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """`Project.search`'s keyword arguments.

    Everything an unknown value is refused for - a status outside the three,
    a colour outside Phorge's ten, a user, project or Space that does not
    exist - is refused by `Project.search` itself, so a spec and the command
    line are answered by the same check with the same message.
    """
    from phabfive.constants import PROJECT_STATUS_ACTIVE

    return {
        "query": values.get("text_query"),
        "members": _as_list(values.get("members")),
        "parents": _as_list(values.get("parents")),
        "ancestors": _as_list(values.get("ancestors")),
        "milestones": _as_bool(values.get("milestones"), "milestones"),
        "status": values.get("status") or PROJECT_STATUS_ACTIVE,
        "icons": _as_list(values.get("icons")),
        "colors": _as_list(values.get("colors")),
        # None means PHAB_SPACE, which is not the same as "no Space filter"
        "spaces": _as_list(values.get("spaces")) or None,
        "show_policy": bool(values.get("show-policy")),
        "show_members": bool(values.get("show-members")),
        "limit": _limit(values.get("limit")),
    }


def _paste_author_constraint() -> str:
    """What `paste.search` calls its author filter.

    ``authors``, where `maniphest.search` calls the same filter
    ``authorPHIDs`` - a wrong key fails with ERR-INVALID-CONSTRAINT. One
    `Field` carries both spellings, which is what `Field.constraints` is
    for, so the quirk is declared once and read here rather than restated.
    """
    field = field_by_name("author", "paste", "search")

    return (constraint_for(field, "paste") if field else None) or "authors"


def _paste_params(app: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """`Paste.paste_search`'s keyword arguments.

    The author is resolved to a PHID here rather than at run time, so a spec
    naming a user who does not exist says so before anything is fetched -
    the same point in the search a bad status pattern is refused at.
    """
    constraints: dict[str, Any] = {}

    text_query = values.get("text_query")
    if text_query:
        constraints["query"] = str(text_query)

    author = values.get("author")
    if author:
        from phabfive.users import resolve_user_phid

        author_phid, _ = resolve_user_phid(app.phab, str(author), option="author")
        constraints[_paste_author_constraint()] = [author_phid]

    return {
        "constraints": constraints or None,
        "limit": _limit(values.get("limit")),
    }


def _passphrase_params(app: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """`Passphrase.search_passphrases`'s keyword arguments.

    ``need_secrets`` is not a spec key and is always False: the name and
    type filters are applied in Python over every credential the token can
    see, so asking a *search* for secret material would fetch the secret of
    every credential on the instance rather than of the ones that matched.
    `passphrase show` is how a secret is read, by id.
    """
    credential_type = values.get("type")

    return {
        "query": values.get("text_query"),
        "credential_type": str(credential_type) if credential_type else None,
        "need_secrets": False,
        "limit": _limit(values.get("limit")),
    }


#: One row per object type. Appended to, never reordered.
_SEARCH_RUNNERS = {
    "task": SearchRunner(
        object_type="task",
        module="phabfive.maniphest",
        app="Maniphest",
        method="task_search",
        # `build` is None: a task search is planned by
        # `phabfive.spec.search.plan_search`, which is the port of everything
        # `maniphest search` used to do inline, and is not restated here.
        records_key="tasks",
        # `SearchPlan.has_criteria` answers this one, from its own list.
        text_key=("text_query",),
    ),
    "project": SearchRunner(
        object_type="project",
        module="phabfive.project",
        app="Project",
        method="search",
        keys=(
            "text_query",
            "members",
            "parents",
            "ancestors",
            "milestones",
            "status",
            "icons",
            "colors",
            "spaces",
            "show-policy",
            "show-members",
            "limit",
        ),
        build=_project_params,
        records_key="projects",
        # Deliberately empty: `project search` with no filter lists every
        # active project in PHAB_SPACE, which is a request, not a mistake.
        criteria=(),
        text_key=("query",),
    ),
    "paste": SearchRunner(
        object_type="paste",
        module="phabfive.paste",
        app="Paste",
        method="paste_search",
        keys=("text_query", "author", "limit"),
        build=_paste_params,
        records_key="pastes",
        criteria=("constraints",),
        text_key=("constraints", "query"),
    ),
    "passphrase": SearchRunner(
        object_type="passphrase",
        module="phabfive.passphrase",
        app="Passphrase",
        method="search_passphrases",
        keys=("text_query", "type", "limit"),
        build=_passphrase_params,
        # The method answers with the credentials themselves.
        records_key=None,
        criteria=("query", "credential_type"),
        text_key=("query",),
    ),
}

#: Read-only: which app runs a search is this module's answer, not a
#: caller's.
SEARCH_RUNNERS: Mapping[str, SearchRunner] = MappingProxyType(_SEARCH_RUNNERS)


def runner_for(object_type: str) -> SearchRunner:
    """How this object type is searched.

    Raises
    ------
    SearchPlanError
        No search runs that type. `check` is "unsupported-type", the same
        one `phabfive.spec.search.plan_search` raises, so a caller answers
        both the same way.
    """
    runner = SEARCH_RUNNERS.get(object_type)

    if runner is None:
        known = ", ".join(sorted(OBJECT_TYPES & set(SEARCH_RUNNERS)))
        raise SearchPlanError(
            f"Cannot search {object_type!r}. A search names one of: {known}",
            check="unsupported-type",
        )

    return runner


def accepted_keys(runner: SearchRunner) -> frozenset[str]:
    """The keys this object type's `search:` section may hold.

    The registry when it declares any for this object type, the runner's own
    table until it does. Task search is declared; project, paste and
    passphrase search are not yet, and a key set has to exist before the
    search can refuse a key nothing reads - which is the drift the registry
    exists to end (#295).

    `tests/test_spec_search_apps.py` asserts the two agree the moment the
    registry declares a key for one of these types, so the table cannot
    quietly outlive it.
    """
    declared = spec_keys(runner.object_type, "search")

    return declared or frozenset(runner.keys)


def _check_keys(runner: SearchRunner, params: Any, *, where: str) -> None:
    """Refuse a `search:` section that is not one, or a key nothing reads.

    `phabfive.spec.search.check_search_params` itself, handed this type's
    key set - not a second copy of its wording, so a person reading the
    message cannot tell which object type refused them and the four cannot
    drift apart.
    """
    check_search_params(
        params,
        where=where,
        object_type=runner.object_type,
        keys=accepted_keys(runner),
    )


def _value(params: Mapping[str, Any], overrides: Mapping[str, Any], key: str) -> Any:
    """One key's value: the override, else the spec's, else None.

    An override that is `None` counts as absent, which is what makes a
    command's sentinels work: typer has no "unset" marker, so a flag nobody
    typed arrives as `None` and must not beat the spec's value.
    """
    supplied = overrides.get(key)

    if supplied is not None:
        return supplied

    return params.get(key)


def app_for(object_type: str, parent: Any) -> Any:
    """The app that searches this object type, sharing `parent`'s client.

    `parent` is any already-constructed app. The sibling is built with
    `Phabfive._from_parent`, which is the rule `Diffusion.passphrase` and
    `Edit.maniphest` already follow: constructing it instead would read the
    configuration again and connect a second time to the same host. A parent
    that is already the right class is handed back as it is.

    Something that is not a `phabfive.core.Phabfive` at all but answers this
    type's search method is handed back too, which is what lets a caller
    stand in for an app - the courtesy `phabfive.spec.search.SearchApp`
    extends by being a protocol. An app is checked by class rather than by
    method, because `Project.search` and `User.search` are two different
    searches with one name.

    Raises
    ------
    SearchPlanError
        No search runs that object type.
    """
    from phabfive.core import Phabfive

    runner = runner_for(object_type)
    app_class = getattr(import_module(runner.module), runner.app)

    if isinstance(parent, app_class):
        return parent

    if not isinstance(parent, Phabfive) and hasattr(parent, runner.method):
        return parent

    return app_class._from_parent(parent)


def plan_item(
    app: Any,
    item: Mapping[str, Any],
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    index: int = 1,
    total: int = 1,
    source: str = DATA_SOURCE,
) -> SearchPlan:
    """Interpret one `searches:` item into a runnable plan, whatever it searches.

    A task item is handed to `phabfive.spec.search.plan_search` unchanged;
    every other type is built from the table above. Either way the plan's
    `params` are exactly the keyword arguments that type's search method
    takes, already interpreted.

    Parameters
    ----------
    app : Phabfive
        The app for *this item's* object type, already constructed. See
        :func:`app_for`, and :func:`run_spec` which pairs them up.
    item : mapping
        One item as `Spec.items("search")` yields it.
    overrides : mapping, optional
        Values that beat the spec's, keyed exactly as a spec key is spelled.
        A key whose value is `None` counts as not supplied. Applied to every
        item, so a key two object types share - `text_query`, `limit` - is
        overridden for both.
    index, total : int
        This search's 1-based position and how many the spec holds.
    source : str
        Where the spec came from, for messages.

    Raises
    ------
    SearchPlanError
        A pattern that does not parse, a task id that is not one, or a
        `type:` nothing can search.
    PhabfiveDataException
        The item's `search:` is not a mapping, or names a key nothing reads.
    PhabfiveInputException
        A value of the wrong shape for its key.
    """
    if not isinstance(item, Mapping):
        raise PhabfiveDataException(
            f"searches[{index - 1}] in {source} is a {type(item).__name__}, "
            f"not a mapping"
        )

    runner = runner_for(str(item.get("type") or DEFAULT_SEARCH_TYPE))

    if runner.build is None:
        return plan_search(
            app, item, overrides=overrides, index=index, total=total, source=source
        )

    params = item.get("search", {})
    _check_keys(runner, params, where=f"searches[{index - 1}]")

    values = {
        key: _value(params, overrides or {}, key) for key in accepted_keys(runner)
    }

    return SearchPlan(
        object_type=runner.object_type,
        params=runner.build(app, values),
        title=item.get("title"),
        description=item.get("description"),
        index=index,
        total=total,
        source=source,
        # The endpoint's answer shape is declared once, here, and the plan
        # carries it so `SearchResult.records` is right for every type
        # rather than only for a task search.
        records_key=runner.records_key,
    )


def run_item(app: Any, plan: SearchPlan) -> SearchResult:
    """Run one plan and return what the app answered.

    The payload is not translated: each app answers with the structure its
    own display consumes, and :func:`records_of` is the uniform view over
    all of them.

    Raises
    ------
    PhabfiveConfigException, PhabfiveDataException, PhabfiveRemoteException
        The app refused the search, or the instance did.
    """
    runner = runner_for(plan.object_type)

    if runner.build is None:
        return run_search(app, plan)

    return SearchResult(plan=plan, payload=getattr(app, runner.method)(**plan.params))


def run_spec(
    parent: Any,
    spec: "Spec",
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Iterator[SearchResult]:
    """Run every search a spec holds, in document order.

    The one call #463 asks for: a spec naming tasks, projects, pastes and
    credentials runs through this, whatever mixture it holds, with one
    configuration and one client behind all of them.

    A generator, so each search runs as its result is taken and a spec whose
    third search fails has already answered its first two - which is the
    order the commands print in. A caller wanting every plan validated
    before anything runs builds the plans first, with :func:`plan_item`.

    Parameters
    ----------
    parent : Phabfive
        Any already-constructed app. The app for each object type is built
        from it with `_from_parent`, once per type however many items name
        it.
    spec : Spec
        A search spec, rendered if it declares variables.
    overrides : mapping, optional
        Values that beat the spec's, applied to every item.
    """
    items = spec.items("search")
    total = len(items)
    apps: dict[str, Any] = {}

    for index, item in enumerate(items, start=1):
        object_type = str(item.get("type") or DEFAULT_SEARCH_TYPE)

        if object_type not in apps:
            apps[object_type] = app_for(object_type, parent)

        app = apps[object_type]
        plan = plan_item(
            app,
            item,
            overrides=overrides,
            index=index,
            total=total,
            source=spec.source,
        )

        yield run_item(app, plan)


def records_of(result: SearchResult) -> list:
    """The objects one search found, as a plain list, whatever it searched.

    `SearchResult.payload` is what the app answered - ``{"projects": [...]}``
    from a project search, ``{"pastes": [...]}`` from a paste search, the
    credentials themselves from a passphrase search - and this is the one
    view over all of them.
    """
    # The plan carries the key, put there by `plan_item` from the runner
    # table above, so this and `SearchResult.records` cannot disagree.
    return result.records


def has_criteria(plan: SearchPlan) -> bool:
    """Whether this search asked for anything.

    False means it would read the whole instance, which a command answers
    with its help rather than running. A type whose runner names no criteria
    - `project` - is always True: listing every active project is a request
    rather than a mistake.
    """
    runner = runner_for(plan.object_type)

    if runner.build is None:
        return plan.has_criteria

    if not runner.criteria:
        return True

    return any(plan.params.get(name) for name in runner.criteria)


def searched_text(plan: SearchPlan) -> Any:
    """The free text this search matched on, or None.

    For the hint a command prints when a text search found nothing, which is
    why it follows each type's own parameter shape rather than guessing.
    """
    value: Any = plan.params

    for key in runner_for(plan.object_type).text_key:
        if not isinstance(value, Mapping):
            return None

        value = value.get(key)

        if value is None:
            return None

    return value
