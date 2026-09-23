# -*- coding: utf-8 -*-
"""Planning what a create spec would create, as data, before anything is written.

Creating is two steps with an inspectable object between them, the shape
`phabfive/edit/plan.py` already proves for an edit and `phabfive/spec/search.py`
for a search::

    create spec (Spec)  --plan-->  CreatePlan  --apply-->  CreateRecord per object

:func:`plan_create` is the first step and :func:`apply_plan` the second, and
neither prints, prompts, exits or reads a terminal. Everything that used to
live inside `Maniphest.create_tasks_from_config` as four hundred lines of
recursion - parsing, rendering, resolving, building transactions, committing
and printing a dry run in one pass - is here as data, with the printing left
to the command.

**One deliberate asymmetry with the search side, and it is not an oversight:
a search spec gets a plan per item, a create spec gets one plan for the whole
document.** Searches are independent and run one at a time, so each carries
its own `SearchPlan`; creates are a dependency graph applied as one ordered
batch, where a task cannot be created before the project it is tagged into,
and a report has to span the whole run to say what exists after a failure
halfway through. So :class:`CreatePlan` holds every :class:`CreateItem` the
document describes, already in apply order.

What the port keeps, and what each of these costs if it is lost:

- **Everything is resolved before anything is written.** One offline pass,
  then one online pass over the whole document, then the transactions. An
  unknown name in the tenth task leaves nothing created.
- **Each distinct name is resolved once**, however many items name it. That
  is `phabfive.spec.online.resolve_references`' whole contract, and it is why
  the answers come back with the problems rather than being asked for twice.
- **Nested children link with `parents.add`**, never by editing the parent.
- **Every collection field is emitted as `.add`, never `.set`.** On an object
  being created the two are identical, so uniformity costs nothing and
  removes the class of bug where a `subtasks.set` on an object that already
  exists silently discards what it had. :func:`apply_item` refuses to send a
  `.set` or a `.remove` at all, so the rule is a guard and not only a
  convention - grep this module for ``.set`` and find none.

Library code: nothing here prints, prompts, exits, reads the environment or
reads a configuration file, and nothing here imports `phabfive.cli`. The app
is handed in already constructed, exactly as `phabfive.spec.online` takes one.
`phabfive.maniphest` is imported inside the functions that need it, never at
module level: `tests/test_spec_isolation.py` imports every module of this
subpackage in a fresh interpreter and asserts that `phabfive.core`,
`phabricator` and `requests` did not arrive with it.

`$local-id` is not Jinja and the reason is in
`phabfive.spec.references`' module docstring; it is not restated here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional

from phabfive.exceptions import (
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.spec.envelope import DATA_SOURCE, Kind, Metadata
from phabfive.spec.online import (
    Resolution,
    Resolver,
    resolve_references,
)
from phabfive.spec.problems import Layer, Problem, Severity, problem
from phabfive.spec.references import (
    ANCHOR_KEYS,
    CREATE_OBJECT_KEYS,
    CREATION_KEYS,
    LOCAL_ID_KEY,
    NESTED_KEY,
    local_id,
)

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from phabfive.spec.envelope import Spec

__all__ = [
    "COLLECTION_FIELDS",
    "CREATABLE_TYPES",
    "EDIT_ENDPOINTS",
    "CreateFailed",
    "CreateItem",
    "CreatePlan",
    "CreatePlanError",
    "CreateRecord",
    "CreateReport",
    "apply_item",
    "apply_plan",
    "plan_create",
    "substitute",
]


#: The object types this module plans. Widened as each app's builder lands;
#: a `pastes:` section in a spec is refused by name until then rather than
#: planned as something it is not.
#:
#: `project` is here because it is what a local reference is *for*: a task
#: tagged into a project the same document creates needs a PHID that does
#: not exist until apply time, and a spec that could only create tasks had
#: nothing to point `$platform` at (#483).
CREATABLE_TYPES: frozenset[str] = frozenset({"task", "project"})

#: Which Conduit application creates each object type. One row per type, so
#: a new creator is a row rather than a branch.
EDIT_ENDPOINTS: Mapping[str, str] = {
    "task": "maniphest",
    "project": "project",
    "paste": "paste",
}

#: The task transactions that take a list. Every one of them is emitted with
#: an ``.add`` suffix - see the module docstring for why there is no `.set`
#: anywhere in this file, and `apply_item` for the guard that keeps it so.
COLLECTION_FIELDS: tuple[str, ...] = (
    "projects",
    "subscribers",
    "parents",
    "subtasks",
)

# Which spec key sets which policy, per object type. The value of each is a
# policy *slot*, which is also what the transaction is called:
# `phabfive.constants.TASK_POLICY_TRANSACTIONS` and its project twin both map
# the slot to itself, and both are read rather than restated.
_POLICY_KEYS: Mapping[str, Mapping[str, str]] = {
    "task": {"visible-to": "view", "editable-by": "edit"},
    "project": {"visible-to": "view", "editable-by": "edit", "joinable-by": "join"},
    "paste": {"visible-to": "view", "editable-by": "edit"},
}


class CreatePlanError(PhabfiveInputException):
    """A create spec that cannot be planned, and which check refused it.

    `check` is a stable name for the check that refused it -
    "unsupported-kind", "unsupported-type", "unsafe-transaction",
    "unresolved-local-id" - so a caller can answer each one differently
    without matching on the message.

    Spelled `check` rather than `code` for the same reason
    `phabfive.spec.search.SearchPlanError` spells it that way: under
    `phabfive/spec/` a `code=` is a `phabfive.spec.problems.Problem` slug,
    and the two vocabularies are kept apart. These name a raise; a `Problem`
    is reported.

    A `PhabfiveInputException`, so every handler that already answers "that
    argument value is not one phabfive can use" answers this too. A plan is
    an input to apply, so a plan that cannot be sent is an input problem.
    """

    def __init__(
        self,
        message: str,
        *,
        check: str,
        object_type: Optional[str] = None,
        path: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.check = check
        self.object_type = object_type
        self.path = path


@dataclasses.dataclass(frozen=True)
class CreateItem:
    """One object a spec would create, or one it only points at.

    Attributes
    ----------
    object_type : str
        "task" today; "project" and "paste" as their builders land.
    path : str
        Where the item is, as `phabfive.spec.references.SpecObject.path`
        spells it and a `Problem` names it: "tasks[0].tasks[2]". This is the
        item's identity inside a plan - `depends_on` and `parent_path` hold
        paths, not local ids, because an item without an `id:` is still
        something a child depends on.
    local_id : str or None
        The item's `id:`, the name a `$ref` elsewhere in the spec reaches it
        by. None for the items nothing refers to, which is most of them.
    transactions : tuple of mapping
        Ready for `<app>.edit`, with every existing reference already a PHID
        and every `$local-id` left as the literal string it was written as -
        substituted by :func:`apply_plan` once the object it names exists.
        Empty for an anchor, which is never sent.
    depends_on : tuple of str
        The paths of the items that must exist first: this item's parent in
        the nesting, and whatever its `$refs` name.
    display : mapping
        The human view - titles, usernames, project names - so a dry run can
        be printed without resolving anything a second time and without the
        command knowing what a PHID is.
    depth : int
        Nesting depth, 0 for an item written at the top of its section. What
        the dry-run tree indents by.
    anchor : bool
        True for an item that creates nothing: it is read for its PHID, or
        it is a bare container holding children. An anchor is **never**
        written - `apply_item` sends no `objectIdentifier`, ever, so a create
        spec has no code path that can edit an existing object.
    phids : tuple of str
        What an anchor's children hang off: the existing objects the anchor
        named, resolved at plan time. Empty for everything this spec
        creates, whose PHID exists only once it has been applied, and empty
        for a bare container, which names nothing for its children to hang
        off - exactly as a titleless grouping has always behaved.

        A tuple rather than one PHID because `parents:` is plural and means
        it: an anchor naming two tasks hangs its children off both. A
        `$local-id` among them stays the literal string it was written as
        and is substituted at apply time, the same way a transaction's is.
    parent_path : str or None
        The item this one is nested under, when it is nested. Its PHIDs
        become a `parents.add` at apply time, which is the one transaction
        that cannot be built while planning because the parent does not
        exist yet.

    Not hashable: `display` is a mapping, so a generated `__hash__` would
    exist and raise the first time an item went into a set.
    """

    object_type: str
    path: str
    local_id: Optional[str] = None
    transactions: tuple[Mapping[str, Any], ...] = ()
    depends_on: tuple[str, ...] = ()
    display: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    depth: int = 0
    anchor: bool = False
    phids: tuple[str, ...] = ()
    parent_path: Optional[str] = None

    __hash__ = None  # type: ignore[assignment]

    @property
    def creates(self) -> bool:
        """Whether applying this item writes anything."""
        return not self.anchor

    @property
    def title(self) -> Optional[str]:
        """What the item would be called, when it has a name at all."""
        value = self.display.get("title")

        return value if isinstance(value, str) else None

    def as_record(self) -> dict[str, Any]:
        """The item as a plain dict, which survives `json.dumps`.

        This is what makes a plan inspectable and serializable without
        applying it: a frontend renders the record, a test asserts a whole
        expected list at once, and a `--format=json` reader sees the same
        keys either way.
        """
        return {
            "type": self.object_type,
            "path": self.path,
            "id": self.local_id,
            "depth": self.depth,
            "anchor": self.anchor,
            "phids": list(self.phids),
            "parent": self.parent_path,
            "depends_on": list(self.depends_on),
            "display": dict(self.display),
            "transactions": [dict(one) for one in self.transactions],
        }


@dataclasses.dataclass(frozen=True)
class CreatePlan:
    """Everything one create spec would create, in the order it would be.

    Attributes
    ----------
    items : tuple of CreateItem
        Already in apply order: a parent before its children, and whatever a
        `$ref` names before the item that names it. Ties are broken by
        document order, which is what makes the order deterministic and
        therefore testable.
    metadata : Metadata or None
        What the spec said about itself, carried so a caller that holds only
        the plan can still name the document.
    source : str
        Where the spec came from, for messages only. Not part of equality:
        the same plan read from a file and from a dict is the same plan.

    Not hashable, for the same reason `CreateItem` is not.
    """

    items: tuple[CreateItem, ...] = ()
    metadata: Optional[Metadata] = None
    source: str = dataclasses.field(default=DATA_SOURCE, compare=False)

    __hash__ = None  # type: ignore[assignment]

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[CreateItem]:
        return iter(self.items)

    @property
    def creating(self) -> tuple[CreateItem, ...]:
        """The items that would actually be written, anchors left out."""
        return tuple(item for item in self.items if item.creates)

    def by_local_id(self) -> Mapping[str, CreateItem]:
        """The items that declare an `id:`, keyed by it.

        Duplicates cannot reach here: the offline pass reports a second
        `id: platform` as `duplicate-local-id` and the plan is refused.
        """
        return {item.local_id: item for item in self.items if item.local_id is not None}

    def by_path(self) -> Mapping[str, CreateItem]:
        """Every item, keyed by the path that is its identity."""
        return {item.path: item for item in self.items}

    def items_of(self, object_type: str) -> tuple[CreateItem, ...]:
        """The items of one object type, in apply order."""
        return tuple(item for item in self.items if item.object_type == object_type)

    def counts(self) -> Mapping[str, int]:
        """How many objects of each type would be created, anchors excluded.

        What a confirmation names before anything is written: a project
        cannot be undone through Conduit, so "3 tasks, 1 project" is the
        sentence a person answers yes to.
        """
        counted: dict[str, int] = {}

        for item in self.creating:
            counted[item.object_type] = counted.get(item.object_type, 0) + 1

        return counted

    def as_records(self) -> list[dict[str, Any]]:
        """The plan as plain dicts, one per item, in apply order."""
        return [item.as_record() for item in self.items]


@dataclasses.dataclass(frozen=True)
class CreateRecord:
    """What became of one item when the plan was applied.

    One per item, yielded as it happens, so a caller sees progress and - the
    point - still holds a record for every object once something fails
    halfway through.

    `status` is "created", "failed" or "skipped". "skipped" is what every
    item after a failure gets: nothing was sent for it and nothing exists,
    which is a different thing from having been tried and refused.

    `error` is the exception a failure came from, kept off `as_record` and
    out of equality because it is for a caller that wants to re-raise, not
    for the report.
    """

    object_type: str
    path: str
    status: str
    local_id: Optional[str] = None
    phid: Optional[str] = None
    id: Optional[int] = None
    monogram: Optional[str] = None
    title: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[BaseException] = dataclasses.field(
        default=None, compare=False, repr=False
    )

    @property
    def created(self) -> bool:
        return self.status == "created"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def label(self) -> str:
        """How a sentence names this object.

        By local id where it has one, because that is the name a spec gave
        it and the name a later run recognises it by; by monogram where it
        has been created and has one; by path otherwise, which is always
        there. The record itself carries all three - this is for the one
        line a person reads.
        """
        named = f"${self.local_id}" if self.local_id else self.monogram

        if named is None and self.title is None:
            # Nothing the spec called it and nothing it became: where it
            # was written is all there is to name it by
            return f"{self.object_type} {self.path}"

        parts = [self.object_type, named, f"{self.title!r}" if self.title else None]

        return " ".join(part for part in parts if part)

    def as_record(self) -> dict[str, Any]:
        """The {local_id, type, path, status, ...} dict, mirroring `Problem`."""
        return {
            "local_id": self.local_id,
            "type": self.object_type,
            "path": self.path,
            "status": self.status,
            "phid": self.phid,
            "id": self.id,
            "monogram": self.monogram,
            "title": self.title,
            "reason": self.reason,
        }


@dataclasses.dataclass(frozen=True)
class CreateReport:
    """Every record of one run, and whether it finished.

    The convenience over the generator, for a caller that does not want to
    watch it happen. `ok` is False when anything failed, which is what the
    command turns into exit status 1.
    """

    records: tuple[CreateRecord, ...] = ()

    __hash__ = None  # type: ignore[assignment]

    @property
    def created(self) -> tuple[CreateRecord, ...]:
        return tuple(record for record in self.records if record.status == "created")

    @property
    def failures(self) -> tuple[CreateRecord, ...]:
        return tuple(record for record in self.records if record.status == "failed")

    @property
    def skipped(self) -> tuple[CreateRecord, ...]:
        return tuple(record for record in self.records if record.status == "skipped")

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def task_ids(self) -> list[int]:
        """The ids of the tasks that were created, in the order they were."""
        return [
            record.id
            for record in self.created
            if record.object_type == "task" and record.id is not None
        ]

    def as_records(self) -> list[dict[str, Any]]:
        return [record.as_record() for record in self.records]

    @property
    def summary(self) -> str:
        """One line saying what exists now, for a caller that prints one.

        Counts and the one failure, not a list: a spec creating seventy
        objects has a record per object for the list, and a sentence nobody
        can read is not a summary. `as_records` is the precise answer, and
        it is the one a program reads.
        """
        created = len(self.created)
        total = len(self.records)
        failures = self.failures

        if not failures:
            return f"{created} of {total} objects created."

        skipped = len(self.skipped)
        sentence = (
            f"{created} of {total} objects created. "
            f"{failures[0].label} failed: {failures[0].reason}"
        )

        if skipped:
            noun = "object" if skipped == 1 else "objects"
            sentence += f". {skipped} {noun} not attempted"

        return sentence + "."

    def raise_for_failure(self) -> "CreateReport":
        """Raise `CreateFailed` if anything failed; hand the report back if not.

        For a caller that answers with a value rather than by yielding -
        `Maniphest.create_tasks_from_config` answers with a dict - and so
        cannot report a partial run any other way. Raising the server's own
        exception instead is what used to lose the records of everything
        that had already been created, which is the whole of #485.

        The server's exception stays the `__cause__`, so the traceback
        still says what the instance refused.
        """
        if self.ok:
            return self

        raise CreateFailed(self) from self.failures[0].error


class CreateFailed(PhabfiveDataException):
    """A run that stopped partway, carrying every record of it.

    Conduit has no transactions, so a spec whose fiftieth object is refused
    has left forty-nine real objects behind. `report` is what exists and
    what does not: one `CreateRecord` per object, `created`, `failed` or
    `skipped`, the same records `apply_plan` yielded.

    A `PhabfiveDataException` for the reason `PhabfiveValidationException`
    is one: every handler that already answers "the data could not be
    used" answers this too, and the structure is there for the handler
    that wants to report per object rather than in one line.
    """

    def __init__(self, report: CreateReport, message: Optional[str] = None) -> None:
        super().__init__(message or report.summary)
        self.report = report


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def plan_create(
    app: Any,
    spec: "Spec",
    *,
    validate: bool = True,
    resolution: Optional[Resolution] = None,
    resolvers: Optional[Sequence[Resolver]] = None,
) -> CreatePlan:
    """Everything a create spec would create, resolved and ordered.

    Atomic in the way `phabfive.spec.search.plan_searches` already is:
    nothing is built if anything failed, so a spec whose tenth task names a
    user that does not exist leaves nothing created and says so once.

    Parameters
    ----------
    app : Phabfive
        An already-constructed app, e.g. a `Maniphest`. Used through
        `app.phab` and for the one piece of interpretation that is the
        app's - validating a priority. Nothing is constructed here and no
        configuration is read.
    spec : Spec
        A create spec, **rendered** if it declares variables: a title still
        holding `{{ sprint }}` is not the title of anything.
    validate : bool
        Whether to run the checks. False runs neither validation pass and
        reports nothing, and that is **all** it means: the online pass that
        turns a name into a PHID still happens, because a plan whose
        references were silently dropped is not the same plan with the
        checking turned off - it is a spec that would create bare objects.
        The shape checks that decide what a value *means* - a `priority:`
        that is not a priority name - always run.
    resolution : Resolution, optional
        What `phabfive.spec.online.resolve_references` answered, for a
        caller that has already made that pass. Used as given and never
        added to, so nothing is looked up twice and nothing is looked up
        behind the caller's back - which also means that an **empty**
        `Resolution()` is a way of asking for a plan with every reference
        dropped. That is the shape of the document and not something to
        apply; `tests/test_create_port_parity.py` uses it to count what a
        template walks to without an instance to resolve against.
    resolvers : sequence of Resolver, optional
        The resolvers to resolve with, in place of
        `phabfive.spec.online.DEFAULT_RESOLVERS`.

    Returns
    -------
    CreatePlan

    Raises
    ------
    CreatePlanError
        The spec is not a create spec, or it holds an object type nothing
        creates yet.
    PhabfiveConfigException
        A value that is not one of the kind its key takes - a priority that
        is not a priority name, a `subscribers:` that is not a list. Raised
        rather than reported, because these are the messages
        `maniphest create --with` has always answered with.
    PhabfiveDataException
        The spec is wrong: every problem both layers found, named at once.
    """
    if spec.kind is not Kind.CREATE:
        raise CreatePlanError(
            f"This plans a create spec, not a {spec.kind.value!r} one",
            check="unsupported-kind",
        )

    walked = tuple(_walk(spec))

    for entry in walked:
        if entry.object_type not in CREATABLE_TYPES:
            known = ", ".join(sorted(CREATABLE_TYPES))
            raise CreatePlanError(
                f"{entry.path}: nothing creates a {entry.object_type!r} from a "
                f"spec yet. A create spec holds: {known}",
                check="unsupported-type",
                object_type=entry.object_type,
                path=entry.path,
            )

    tasks = tuple(entry for entry in walked if entry.object_type == "task")

    # Before anything is fetched, so a typo in a priority costs no request
    # and leaves nothing half-created (#465).
    priorities = {entry.path: _priority(entry) for entry in tasks}
    users = {entry.path: _users(entry) for entry in tasks}
    projects = {entry.path: _projects(entry) for entry in tasks}
    statuses = _statuses(app, tasks)

    problems: list[Problem] = list(_required(walked))

    if validate:
        problems = list(spec.validate_offline()) + problems

    answers = resolution

    if answers is None and not _errors(problems):
        # Unconditionally, whatever `validate` says: `validate` decides
        # whether problems are reported, never whether a name becomes a
        # PHID. Skipping this would build every reference-valued key as
        # nothing - a task with `projects:` and `assignment:` planned down
        # to a title and a priority - which is the silent loss this whole
        # phase exists to close.
        #
        # The defaults answer for every kind a create spec names, tasks
        # included: `TaskResolver` is one of them since #482, so a `parents:
        # [T1]` in a spec and a `parents: [T1]` a `spec validate` is run
        # over are answered by the same request.
        answers = resolve_references(spec, app, resolvers=resolvers)

        if validate:
            problems += list(answers.problems)

    if answers is None:
        # The offline pass already refused the spec, so there is nothing
        # worth asking the instance about. The raise below happens next.
        answers = Resolution()

    # One `Project` sibling for the whole document, built only for a spec
    # that names a project: the module does each thing once per document,
    # and two siblings for one plan would be two of the same thing.
    builder = (
        _project_app(app)
        if any(entry.object_type == "project" for entry in walked)
        else None
    )

    if validate and builder is not None and not _errors(problems):
        problems += _hashtag_problems(builder, walked)

    # After the resolution, because a column is looked for on the boards the
    # task's `projects:` name and those are PHIDs by now. Unconditional in
    # `validate`, like `_required` and `_priority`: `validate` says whether
    # the *spec* is checked, and a column nobody has is not a transaction
    # this could build and leave out in silence.
    columns: dict[str, str] = {}

    if not _errors(problems):
        columns, column_problems = _columns(app, tasks, answers)
        problems += column_problems

    errors = _errors(problems)

    if errors:
        raise PhabfiveDataException(_report(errors))

    policies: dict[str, Any] = {}

    items = tuple(
        _task_item(
            entry,
            resolution=answers,
            priority=priorities[entry.path],
            status=statuses.get(entry.path),
            column=columns.get(entry.path),
            users=users[entry.path],
            projects=projects[entry.path],
            policies=_policies(app, entry, policies),
        )
        if entry.object_type == "task"
        else _project_item(
            entry,
            resolution=answers,
            builder=builder,
            policies=_policies(app, entry, policies),
        )
        for entry in walked
    )

    return CreatePlan(
        items=_ordered(_linked(items)),
        metadata=spec.metadata,
        source=spec.source,
    )


@dataclasses.dataclass(frozen=True)
class _Entry:
    """One item of the walk: where it is, how deep, and what is above it."""

    path: str
    object_type: str
    data: Mapping[str, Any]
    depth: int = 0
    parent_path: Optional[str] = None


def _walk(spec: "Spec") -> Iterator[_Entry]:
    """Every item a create spec holds, in document order, with its nesting.

    `phabfive.spec.references.iter_objects` is the same walk and is what the
    two validation layers use; this one carries the depth and the parent
    path as well, which is what a tree of tasks needs and a report does not.
    The two must agree about paths, because a `Problem` names an item by one.
    """
    for key, object_type in CREATE_OBJECT_KEYS:
        for index, item in enumerate(spec.items(key)):
            if not isinstance(item, Mapping):
                continue

            path = f"{key}[{index}]"
            yield _Entry(path=path, object_type=object_type, data=item)

            if object_type == "task":
                yield from _nested(item, path, depth=1)


def _nested(item: Mapping[str, Any], path: str, *, depth: int) -> Iterator[_Entry]:
    """One task's children, depth first, in file order."""
    children = item.get(NESTED_KEY)

    if not isinstance(children, (list, tuple)):
        return

    for index, child in enumerate(children):
        if not isinstance(child, Mapping):
            continue

        child_path = f"{path}.{NESTED_KEY}[{index}]"
        yield _Entry(
            path=child_path,
            object_type="task",
            data=child,
            depth=depth,
            parent_path=path,
        )
        yield from _nested(child, child_path, depth=depth + 1)


def _creatable(entry: _Entry) -> bool:
    """Whether the item describes an object, or only points at one.

    For a task that is `title:` and for a project `name:`, which
    `phabfive.spec.references.CREATION_KEYS` declares once for both this and
    the offline pass. An item with none of them is an anchor: it holds
    children and creates nothing itself, which is what a template writing a
    bare `tasks:` grouping has always meant, and what `parent: T123` with
    children under it means (#482).
    """
    return any(entry.data.get(key) for key in CREATION_KEYS.get(entry.object_type, ()))


def _anchors(entry: _Entry) -> bool:
    """Whether the item points at objects its children can hang off.

    Only about a task's `parent:`/`parents:`, and only about whether they
    were written at all - what they name is the online pass's question. An
    item with neither is a bare container, whose children hang off nothing,
    exactly as a titleless grouping has always behaved. Nothing nests under
    a project, so a project's `parent:` is a field of the project being
    created and never an anchor.
    """
    return entry.object_type == "task" and bool(_monograms(entry, *ANCHOR_KEYS))


def _required(entries: Sequence[_Entry]) -> list[Problem]:
    """The items that name nothing and hold nothing, reported by name.

    The deliberate behaviour change of #480. A task with neither a `title:`
    nor children used to be dropped with a `log.warning` nobody reads, so a
    template with a mistyped key created one task fewer than it said and
    exited 0. It is `missing-required` now, and the spec is refused.

    `description:` is **not** required, which is the other half of the
    change: the old code took a title and a description or nothing, while
    `maniphest create --title` has never required a description and the
    server does not either.

    An item that names an existing object is not reported either, however
    little else it holds: `- parent: T123` with nothing under it creates
    nothing and links nothing, but it is a spec being written rather than a
    key that was mistyped. A project has neither escape: nothing nests under
    one and nothing anchors to one, so a `projects:` item with no `name:`
    creates nothing whatever else it holds.
    """
    problems: list[Problem] = []

    for entry in entries:
        if _creatable(entry):
            continue

        if entry.object_type == "task":
            if entry.data.get(NESTED_KEY) or _anchors(entry):
                continue

            field = "title"
            reason = (
                "A task needs a title, or child tasks to hold. This item has "
                "neither, so it would create nothing"
            )
        else:
            field = CREATION_KEYS[entry.object_type][0]
            reason = (
                f"A {entry.object_type} is named by its {field}:, and this "
                f"item has none, so it would create nothing"
            )

        problems.append(
            problem(
                entry.path,
                field=field,
                reason=reason,
                code="missing-required",
                layer=Layer.OFFLINE,
            )
        )

    return problems


def _errors(problems: Sequence[Problem]) -> list[Problem]:
    """Only the problems that stop a plan; a warning is reported, never fatal."""
    return [one for one in problems if one.severity == Severity.ERROR]


def _report(errors: Sequence[Problem]) -> str:
    """Every error in one sentence-per-line report.

    The shape `phabfive.spec.search.plan_searches` already raises with, so a
    caller that answers one answers the other, and a person reading either
    sees the same thing.
    """
    return "\n".join(
        [f"{len(errors)} problem(s) in this spec:"]
        + [
            "  - "
            + (f"{one.object}.{one.field}" if one.field else one.object)
            + f": {one.reason}"
            for one in errors
        ]
    )


def _priority(entry: _Entry) -> Optional[str]:
    """One task's priority, in the API's spelling, validated first.

    The same check `--priority` gets and before anything is fetched: an
    unvalidated priority reaches the server verbatim and comes back as an
    opaque Conduit error instead of naming the valid priorities (#465).
    """
    from phabfive.exceptions import PhabfiveConfigException
    from phabfive.maniphest.validators import validate_priority

    value = entry.data.get("priority")

    if value is None:
        return None

    # A `-` in YAML is null, and 2 is a number; neither names a priority,
    # and neither survives validate_priority's .lower()
    if not isinstance(value, str):
        raise PhabfiveConfigException(f"priority takes a priority name, not {value!r}")

    return validate_priority(value)


def _statuses(app: Any, entries: Sequence[_Entry]) -> dict[str, Optional[str]]:
    """Every task's `status:`, in the API's spelling, for the whole document.

    One `maniphest.querystatuses` for the document rather than one per task,
    and only when a task asked for a status at all - which is the same
    property the online pass has for every other kind.

    `fetch_api_status_map` and not `get_api_status_map`: the latter answers
    a failed fetch with the standard statuses invented locally, and a spec
    refused against invented statuses - or, worse, blessed by them - would
    be phabfive making up what the instance configured. See the note in
    `phabfive/maniphest/fetchers.py` and the cache section of CLAUDE.md.
    """
    from phabfive.exceptions import PhabfiveConfigException

    wanted: dict[str, str] = {}

    for entry in entries:
        value = entry.data.get("status")

        if value is None:
            continue

        if not isinstance(value, str):
            raise PhabfiveConfigException(f"status takes a status name, not {value!r}")

        wanted[entry.path] = value

    if not wanted:
        return {}

    from phabfive.maniphest.fetchers import fetch_api_status_map
    from phabfive.maniphest.validators import validate_status

    status_map = fetch_api_status_map(app.phab)
    settled: dict[str, str] = {}

    for value in wanted.values():
        if value not in settled:
            settled[value] = validate_status(value, status_map)

    return {path: settled[value] for path, value in wanted.items()}


def _columns(
    app: Any, entries: Sequence[_Entry], resolution: Resolution
) -> tuple[dict[str, str], list[Problem]]:
    """Every task's `column:`, as a column PHID on one of its own boards.

    A column means nothing on its own - two boards may both have a "Done" -
    so the board is the task's `projects:`, already PHIDs by the time this
    runs. One `project.column.search` per distinct board for the whole
    document, and a column that is on none of them is `unknown-column`
    rather than a value sent to the server to be refused after the tasks
    before it exist.

    A board the *spec* creates is refused by name: a project that does not
    exist yet has no workboard and no columns, so ``column:`` on a task
    tagged only into `$platform` names nothing that could be found. The
    rule that a `column:` needs a `projects:` at all is offline, in
    `phabfive.spec.validate`, because it is a question about the file.

    A spec sends the column in the **same** `maniphest.edit` that creates
    the task, where `maniphest create --column` creates the task and then
    edits it by `objectIdentifier`. Verified against a real Phorge: with
    `projects.add` and `column` in one create edit the task lands on the
    board in that column.
    """
    from phabfive.exceptions import PhabfiveConfigException

    wanted: dict[str, tuple[str, list[str]]] = {}

    for entry in entries:
        value = entry.data.get("column")

        if value is None:
            continue

        if not isinstance(value, str) or not value.strip():
            raise PhabfiveConfigException(f"column takes a column name, not {value!r}")

        written = _projects(entry)
        boards = _resolved_list(resolution, "task", "projects", written)

        if written and not boards:
            # Its `projects:` were written and none of them was answered:
            # either the resolution reported that already, or the caller
            # handed in one that answers nothing and asked for the shape of
            # the document alone. Either way "no column called 'Doing' on
            # the 0 project(s) this task is tagged into" would be a second
            # problem for one cause, describing the symptom.
            continue

        wanted[entry.path] = (value, boards)

    if not wanted:
        return {}, []

    from phabfive.maniphest.fetchers import get_column_info

    known: dict[str, dict[str, Any]] = {}
    resolved: dict[str, str] = {}
    problems: list[Problem] = []

    for path, (value, boards) in wanted.items():
        found = None
        local = [one for one in boards if local_id(one) is not None]
        real = [one for one in boards if local_id(one) is None]

        for board in real:
            if board not in known:
                known[board] = get_column_info(app.phab, board)

            for column_phid, column in known[board].items():
                if str(column.get("name", "")).lower() == value.lower():
                    found = column_phid
                    break

            if found:
                break

        if found:
            resolved[path] = found
            continue

        if local and not real:
            reason = (
                f"{value!r} is a column on a board, and the only project this "
                f"task is tagged into is one this spec creates ({local[0]}), "
                f"which has no workboard until it exists"
            )
        else:
            reason = (
                f"No workboard column called {value!r} on the "
                f"{len(real)} project(s) this task is tagged into"
            )

        problems.append(
            problem(
                path,
                field="column",
                value=value,
                reason=reason,
                code="unknown-column",
                layer=Layer.ONLINE,
            )
        )

    return resolved, problems


def _users(entry: _Entry) -> tuple[Optional[str], list[str]]:
    """One task's assignment and subscribers, checked for shape only.

    Iterating a string would ask for each letter as a user and a mapping for
    each of its keys, which is how a `subscribers: alice` used to reach the
    instance as five lookups.
    """
    from phabfive.exceptions import PhabfiveConfigException

    assignment = entry.data.get("assignment")

    if assignment is not None and not isinstance(assignment, str):
        raise PhabfiveConfigException(f"assignment takes one user, not {assignment!r}")

    subscribers = entry.data.get("subscribers") or []

    if not isinstance(subscribers, list):
        raise PhabfiveConfigException(
            f"subscribers takes a list of users, not {subscribers!r}"
        )

    # A stray `-` in YAML is a null item, and 1234 is a number
    not_names = [name for name in subscribers if not isinstance(name, str)]

    if not_names:
        raise PhabfiveConfigException(
            f"subscribers takes usernames, not {not_names[0]!r}"
        )

    return assignment or None, list(subscribers)


def _projects(entry: _Entry) -> list[str]:
    """One task's project names, checked for shape only."""
    from phabfive.exceptions import PhabfiveConfigException

    names = entry.data.get("projects") or []

    if not isinstance(names, list):
        raise PhabfiveConfigException(
            f"projects takes a list of project names, not {names!r}"
        )

    for name in names:
        # A `-` in YAML is a null item, and 1234 is a number; neither names
        # a project
        if not isinstance(name, str):
            raise PhabfiveConfigException(f"projects takes project names, not {name!r}")

    return list(names)


def _monograms(entry: _Entry, *keys: str) -> list[str]:
    """One task's `parent:`, `parents:` or `subtasks:`, as a list of strings.

    Several keys at once for `parent:` and `parents:`, which are one
    meaning with two spellings: both name the objects this item hangs off,
    and both end up in the same `parents.add`. Written together in file
    order and deduplicated, so writing a task under both spellings links it
    once.
    """
    values: list[str] = []

    for key in keys:
        value = entry.data.get(key) or []

        if not isinstance(value, (list, tuple)):
            value = [value]

        values += [one for one in value if isinstance(one, str)]

    return list(dict.fromkeys(values))


def _reference(
    resolution: Resolution, object_type: str, field: str, value: str
) -> Optional[str]:
    """What one value becomes in a transaction: a PHID, or the `$ref` itself.

    A `$local-id` is left exactly as written - it names something this spec
    creates, so there is no PHID until it has been - and `apply_plan`
    substitutes it as it goes. Everything else was resolved up front and is
    a PHID by now; `None` means nobody answered, which cannot happen for a
    kind that has a resolver and a spec that validated.

    `object_type` and `field` together decide which lookup answered - a bare
    name in `assignment:` is a user and the same word in `projects:` is a
    project - so both are carried rather than assumed.
    """
    if local_id(value) is not None:
        return value

    return resolution.phid_for(object_type, field, value)


def _task_item(
    entry: _Entry,
    *,
    resolution: Resolution,
    priority: Optional[str],
    status: Optional[str],
    column: Optional[str],
    users: tuple[Optional[str], list[str]],
    projects: Sequence[str],
    policies: Mapping[str, tuple[Any, str]],
) -> CreateItem:
    """One task item: its transactions, what it waits for, and its preview."""
    from phabfive.constants import PRIORITY_DEFAULT, TASK_POLICY_TRANSACTIONS

    declared = entry.data.get(LOCAL_ID_KEY)
    name = declared if isinstance(declared, str) else None

    if not _creatable(entry):
        # An anchor. It creates nothing, so it is never sent; what it names
        # is what its children hang off, which is the motivating case of
        # #482:
        #
        #     tasks:
        #       - parent: T123      # read for its PHID, never written
        #         tasks:
        #           - title: "Subtask A"
        #
        # T123 is not edited - no `subtasks.set` on it, no
        # `objectIdentifier` anywhere - and each child carries a
        # `parents.add` naming it instead, which cannot discard the
        # subtasks T123 already has.
        anchored = _monograms(entry, *ANCHOR_KEYS)
        phids = _resolved_list(resolution, "task", "parents", anchored)

        return CreateItem(
            object_type="task",
            path=entry.path,
            local_id=name,
            depth=entry.depth,
            anchor=True,
            phids=tuple(phids),
            parent_path=entry.parent_path,
            depends_on=tuple(
                dict.fromkeys(
                    ([entry.parent_path] if entry.parent_path else [])
                    + [one for one in phids if local_id(one) is not None]
                )
            ),
            display={"title": None, "anchors": list(anchored)},
        )

    assignment, subscribers = users
    transactions: list[Mapping[str, Any]] = [
        {"type": "title", "value": entry.data["title"]}
    ]

    description = entry.data.get("description")

    # Not required any more: `maniphest create --title` has never wanted one
    # and the server does not either, where the old template path dropped
    # the whole task without one
    if description is not None:
        transactions.append({"type": "description", "value": description})

    transactions.append({"type": "priority", "value": priority or PRIORITY_DEFAULT})

    # After the priority, which is the order `maniphest create` sends them
    # in. Declared in the registry since #481 and dropped on the floor until
    # now: a spec asking for `status: resolved` created an open task and
    # said nothing (#481).
    if status is not None:
        transactions.append({"type": "status", "value": status})

    display: dict[str, Any] = {
        "title": entry.data["title"],
        "priority": priority or PRIORITY_DEFAULT,
        "status": status,
        "assignee": None,
        "subscribers": [],
        "projects": [],
        "space": None,
    }

    if assignment:
        owner = _reference(resolution, "task", "assignment", assignment)

        if owner:
            transactions.append({"type": "owner", "value": owner})
            display["assignee"] = resolution.label_for("task", "assignment", assignment)

    project_phids = _resolved_list(resolution, "task", "projects", projects)

    if project_phids:
        transactions.append({"type": "projects.add", "value": project_phids})
        display["projects"] = [
            resolution.label_for("task", "projects", one) for one in projects
        ]

    # After the `projects.add` it depends on, and in the same edit: the task
    # is put on the board and into the column in one call, so there is no
    # window in which it exists in the wrong place and no second edit
    # carrying an `objectIdentifier`.
    if column is not None:
        transactions.append({"type": "column", "value": [column]})
        display["column"] = entry.data.get("column")

    subscriber_phids, subscriber_names = _resolved_named(
        resolution, "task", "subscribers", subscribers
    )

    if subscriber_phids:
        transactions.append({"type": "subscribers.add", "value": subscriber_phids})
        display["subscribers"] = subscriber_names

    space = entry.data.get("space")

    if isinstance(space, str) and space:
        space_phid = _reference(resolution, "task", "space", space)

        if space_phid:
            transactions.append({"type": "space", "value": space_phid})
            display["space"] = resolution.label_for("task", "space", space)

    for slot, (resolved, shown) in policies.items():
        transactions.append({"type": TASK_POLICY_TRANSACTIONS[slot], "value": resolved})
        display[slot] = shown

    subtasks = _resolved_list(
        resolution, "task", "subtasks", _monograms(entry, "subtasks")
    )

    if subtasks:
        transactions.append({"type": "subtasks.add", "value": subtasks})

    parents = _resolved_list(
        resolution, "task", "parents", _monograms(entry, *ANCHOR_KEYS)
    )

    if parents:
        transactions.append({"type": "parents.add", "value": parents})

    waits_for = [
        value
        for transaction in transactions
        for value in _values(transaction["value"])
        if local_id(value) is not None
    ]

    return CreateItem(
        object_type="task",
        path=entry.path,
        local_id=name,
        transactions=tuple(transactions),
        depends_on=tuple(
            dict.fromkeys(
                ([entry.parent_path] if entry.parent_path else []) + list(waits_for)
            )
        ),
        display=display,
        depth=entry.depth,
        parent_path=entry.parent_path,
    )


def _project_app(app: Any) -> Any:
    """The app whose builder makes a project's transactions.

    `phabfive.spec` may not import an app class - that is the whole reason
    `phabfive.create` exists - so the runner is asked for one, at call time
    and only for a spec that actually names a project. `app_for` hands back
    a sibling built with `Phabfive._from_parent`, so the second app shares
    the first's configuration and client rather than discovering and
    connecting again.
    """
    from phabfive.create.dispatch import app_for

    return app_for("project", app)


def _hashtag_problems(builder: Any, entries: Sequence[_Entry]) -> list[Problem]:
    """The project names an existing project already answers to the hashtag of.

    Layer 2, and the one check that has to be a check rather than a server
    refusal: **a project cannot be deleted or archived through Conduit**, so
    a spec naming five projects must not create four and then stop on the
    fifth. `Project.hashtag_conflicts` asks for the whole document at once
    and says which names clash; the sentence is the command's, so a person
    who has seen it from `project create` recognises it here.

    A milestone is never asked about: it is named after its number within
    its parent, and any number of them may share a name.
    """
    named = {
        entry.path: entry.data["name"]
        for entry in entries
        if entry.object_type == "project"
        and isinstance(entry.data.get("name"), str)
        and entry.data["name"].strip()
        and not entry.data.get("milestone-of")
    }

    if not named:
        return []

    conflicts = builder.hashtag_conflicts(named.values())

    if not conflicts:
        return []

    return [
        problem(
            path,
            field="name",
            value=name,
            reason=builder.hashtag_taken_message(name, conflicts[name]),
            code="hashtag-taken",
            layer=Layer.ONLINE,
        )
        for path, name in named.items()
        if name in conflicts
    ]


def _policies(
    app: Any, entry: _Entry, cache: dict[str, Any]
) -> dict[str, tuple[Any, str]]:
    """One item's policies, as ``{slot: (what to send, what to show)}``.

    `phabfive.policy.resolve_policy_value` is what every policy option on
    every command already goes through, so a spec reads ``#backend``,
    ``@alice``, ``@me``, a keyword and a PHID exactly the way
    ``--visible-to`` does. It is called once per distinct value across the
    whole document, which is the property the online pass has for every
    other kind.

    It is deliberately not the online pass: `FieldKind.POLICY` has no
    resolver in `phabfive.spec.online` yet, so a policy left to it comes
    back unanswered and the key would be **dropped in silence** - a spec
    asking for a restricted view policy creating an object anyone can see.
    Until a `PolicyResolver` lands, this resolves them where the answer is
    used. A `$local-id` is passed through untouched, so a spec may restrict
    an object to a project it creates in the same document.
    """
    from phabfive.policy import resolve_policy_value

    asked: dict[str, tuple[Any, str]] = {}

    for key, slot in _POLICY_KEYS.get(entry.object_type, {}).items():
        value = entry.data.get(key)

        if value is None:
            continue

        if not isinstance(value, str) or not value.strip():
            from phabfive.exceptions import PhabfiveConfigException

            raise PhabfiveConfigException(
                f"{key} takes one policy - a keyword, #project, @user or PHID "
                f"- not {value!r}"
            )

        if local_id(value) is not None:
            asked[slot] = (value, value)
            continue

        if value not in cache:
            cache[value] = resolve_policy_value(app.phab, value, option=key)

        asked[slot] = (cache[value], value)

    return asked


def _strings(entry: _Entry, key: str) -> list[str]:
    """One list-of-text key, checked for shape only."""
    from phabfive.exceptions import PhabfiveConfigException

    values = entry.data.get(key) or []

    if not isinstance(values, list):
        raise PhabfiveConfigException(f"{key} takes a list, not {values!r}")

    for one in values:
        if not isinstance(one, str):
            raise PhabfiveConfigException(f"{key} takes text, not {one!r}")

    return list(values)


def _project_item(
    entry: _Entry,
    *,
    resolution: Resolution,
    builder: Any,
    policies: Mapping[str, tuple[Any, str]],
) -> CreateItem:
    """One project item: its transactions, what it waits for, and its preview.

    The transactions are `Project.project_create_transactions`', not this
    module's: `project create` and a create spec end at the same builder, so
    neither can grow a field the other does not send. What is done here is
    what a spec adds to it - every reference already a PHID from the one
    online pass, and a `$local-id` left literal for `apply_plan` to
    substitute.

    ``check_hashtag=False``: the clash is asked about once for the whole
    document in :func:`_hashtag_problems`, before anything is built, rather
    than one request per project halfway through building.
    """
    data = entry.data
    declared = data.get(LOCAL_ID_KEY)
    name = declared if isinstance(declared, str) else None

    def one(field: str) -> Optional[tuple[str, str]]:
        value = data.get(field)

        if not isinstance(value, str) or not value:
            return None

        phid = _reference(resolution, "project", field, value)

        if phid is None:
            return None

        return phid, resolution.label_for("project", field, value)

    members = [
        (phid, resolution.label_for("project", "members", who))
        for who, phid in (
            (who, _reference(resolution, "project", "members", who))
            for who in _strings(entry, "members")
        )
        if phid
    ]

    slugs = [slug.lstrip("#") for slug in _strings(entry, "slugs") if slug.lstrip("#")]

    transactions, changes = builder.project_create_transactions(
        data["name"],
        description=data.get("description"),
        icon=data.get("icon"),
        color=data.get("color"),
        slugs=slugs,
        members=members,
        parent=one("parent"),
        milestone_of=one("milestone-of"),
        space=one("space"),
        policies=policies,
        check_hashtag=False,
    )

    waits_for = [
        value
        for transaction in transactions
        for value in _values(transaction["value"])
        if local_id(value) is not None
    ]

    return CreateItem(
        object_type="project",
        path=entry.path,
        local_id=name,
        transactions=tuple(dict(one) for one in transactions),
        depends_on=tuple(dict.fromkeys(waits_for)),
        display={
            "title": data["name"],
            # What the project will be known by once it exists, when the
            # spec said so. `project.edit` answers with an id and a PHID and
            # never with the slug Phorge derived, and deriving one here
            # would be phabfive guessing at rules it does not own.
            "hashtag": f"#{slugs[0]}" if slugs else None,
            "changes": list(changes),
        },
        depth=entry.depth,
    )


def _values(value: Any) -> list[str]:
    """One transaction value as a list of strings, whatever shape it has."""
    if isinstance(value, str):
        return [value]

    if isinstance(value, (list, tuple)):
        return [one for one in value if isinstance(one, str)]

    return []


def _resolved_list(
    resolution: Resolution,
    object_type: str,
    field: str,
    values: Sequence[str],
) -> list[str]:
    """Every value of one list key, as PHIDs, deduplicated in order."""
    return list(
        dict.fromkeys(
            phid
            for phid in (
                _reference(resolution, object_type, field, one) for one in values
            )
            if phid
        )
    )


def _resolved_named(
    resolution: Resolution,
    object_type: str,
    field: str,
    values: Sequence[str],
) -> tuple[list[str], list[str]]:
    """The PHIDs of one list key, and what each of them is called.

    The names follow the deduplicated PHIDs rather than the written values,
    so `["alice", "@Alice", "PHID-USER-alice"]` previews as one subscriber,
    which is the one that is subscribed.
    """
    phids: dict[str, str] = {}

    for one in values:
        phid = _reference(resolution, object_type, field, one)

        if phid and phid not in phids:
            phids[phid] = resolution.label_for(object_type, field, one)

    return list(phids), list(phids.values())


def _linked(items: Sequence[CreateItem]) -> tuple[CreateItem, ...]:
    """Every item's `depends_on` as item paths, which is what it says it is.

    An item builder knows the `$local-id` a value was written as and not
    which item declares it, so it records the `$ref` and this pass turns it
    into the path. One vocabulary in the finished plan: `depends_on` holds
    item paths, the same identity `path` and `parent_path` hold, so
    `as_record()` hands a frontend one kind of name rather than two - and an
    item that a `$ref` names but that has no `id:` of its own is still
    something to depend on.

    A `$ref` naming nothing is dropped rather than kept as itself: the
    offline pass reports it as `unknown-local-id`, and a dependency on
    something that is not in the plan is not a dependency.
    """
    by_path = {item.path for item in items}
    by_local = {
        f"${item.local_id}": item.path for item in items if item.local_id is not None
    }

    linked: list[CreateItem] = []

    for item in items:
        needed = tuple(
            dict.fromkeys(
                path
                for path in (by_local.get(one, one) for one in item.depends_on)
                if path in by_path and path != item.path
            )
        )

        linked.append(
            item
            if needed == item.depends_on
            else dataclasses.replace(item, depends_on=needed)
        )

    return tuple(linked)


def _ordered(items: Sequence[CreateItem]) -> tuple[CreateItem, ...]:
    """The items in an order that creates everything before it is needed.

    Kahn's algorithm over two kinds of edge - a child depends on its parent
    item, and an item depends on whatever its `$refs` name - with ties
    broken by document order, which is what makes the order deterministic
    and therefore testable. Both edges are already item paths by now;
    :func:`_linked` is what made them so. A cycle over `$refs` is
    `local-id-cycle`, which the offline pass reports before this is reached;
    nesting cannot cycle. A cycle that reaches here anyway leaves its items
    in document order rather than dropping them, because a plan with a hole
    in it is worse than one in an unhelpful order.
    """
    by_path = {item.path: item for item in items}
    waiting = {item.path: set(item.depends_on) for item in items}

    ordered: list[CreateItem] = []
    placed: set[str] = set()
    remaining = [item.path for item in items]

    while remaining:
        # One at a time, and always the first that is ready: taking a whole
        # level at once would put a second root task ahead of the first
        # one's children, which is not the order the document reads in.
        ready = next((path for path in remaining if waiting[path] <= placed), None)

        if ready is None:
            # A cycle the offline pass should have reported. Keep what is
            # left in document order rather than losing it.
            ordered += [by_path[path] for path in remaining]
            break

        ordered.append(by_path[ready])
        placed.add(ready)
        remaining.remove(ready)

    return tuple(ordered)


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def substitute(
    transactions: Sequence[Mapping[str, Any]], realised: Mapping[str, str]
) -> list[dict[str, Any]]:
    """The transactions with every `$local-id` replaced by what it became.

    One pass over the values, no index bookkeeping, and it survives a plan
    that went through JSON and back - which is the whole reason a local
    reference stays the literal string `"$platform"` in a planned
    transaction rather than becoming a placeholder object.

    Raises
    ------
    CreatePlanError
        A `$ref` naming something that has not been created. That is a
        planner bug - the offline pass proved the name is declared and the
        ordering proved it comes first - so it is refused rather than sent.
    """
    substituted: list[dict[str, Any]] = []

    for transaction in transactions:
        value = transaction.get("value")

        if isinstance(value, str):
            new_value: Any = _realise(value, realised)
        elif isinstance(value, (list, tuple)):
            new_value = [
                _realise(one, realised) if isinstance(one, str) else one
                for one in value
            ]
        else:
            new_value = value

        substituted.append({**transaction, "value": new_value})

    return substituted


def _realise(value: str, realised: Mapping[str, str]) -> str:
    """One value, with a `$local-id` turned into the PHID it names."""
    name = local_id(value)

    if name is None:
        return value

    if name not in realised:
        raise CreatePlanError(
            f"{value!r} names an object this run has not created. Nothing was "
            "sent for it",
            check="unresolved-local-id",
        )

    return realised[name]


def apply_item(
    app: Any,
    item: CreateItem,
    realised: Mapping[str, str],
    paths: Mapping[str, Sequence[str]],
) -> CreateRecord:
    """Create one object, and say what it became.

    **No `objectIdentifier`, ever.** A create spec has no code path that can
    edit an existing object, which is a stronger guarantee than checking
    which transaction types it holds, and it is why an anchor is safe to
    have in a plan at all.

    Parameters
    ----------
    app : Phabfive
        The app for this item's object type, already constructed.
    item : CreateItem
    realised : mapping
        Local id -> the PHID it has been created as, so far this run.
    paths : mapping
        Item path -> the PHIDs a child of that item hangs off, which is how
        a nested child finds its parent: a parent without an `id:` is still
        something to link to. Several, because an anchor may name several
        existing objects - `parents:` is plural and means it.

    Raises
    ------
    CreatePlanError
        A transaction that would change a collection rather than fill one -
        anything ending in `.set` or `.remove`. Nothing is sent.
    """
    transactions = substitute(item.transactions, realised)

    parent_phids = list(paths.get(item.parent_path) or ()) if item.parent_path else []

    if parent_phids:
        # The nesting link, and the one transaction that cannot be built
        # while planning: the parent does not exist yet - or, for an anchor,
        # is not this item's parent at all but what the anchor named.
        # `.add`, never a rewrite of the parent: the parent is not edited,
        # so a subtask list that already exists cannot be discarded.
        transactions.append({"type": "parents.add", "value": parent_phids})

    # After the nesting link is appended rather than before it, so the guard
    # covers every transaction that would actually be sent and not only the
    # ones the planner built.
    for transaction in transactions:
        kind = str(transaction.get("type") or "")

        if kind.endswith(".set") or kind.endswith(".remove"):
            raise CreatePlanError(
                f"{item.path}: a create spec never sends {kind!r}. A collection "
                "is filled with .add, which cannot discard what an object "
                "already has",
                check="unsafe-transaction",
                object_type=item.object_type,
                path=item.path,
            )

    # One endpoint per object type, so #481 adds a creator by adding a row
    # rather than by branching here.
    endpoint = getattr(app.phab, EDIT_ENDPOINTS[item.object_type])
    answer = endpoint.edit(transactions=transactions)
    created = (answer or {}).get("object") or {}
    task_id = created.get("id")

    return CreateRecord(
        object_type=item.object_type,
        path=item.path,
        status="created",
        local_id=item.local_id,
        phid=created.get("phid"),
        id=task_id,
        # A project is known by a hashtag rather than by a monogram, and
        # `project.edit` answers with an id and a PHID but never with the
        # slug Phorge derived from the name, so the plan carries the one the
        # spec asked for and there is none when it asked for none.
        monogram=_monogram(item.object_type, task_id) or item.display.get("hashtag"),
        title=item.title,
    )


def _monogram(object_type: str, id_: Any) -> Optional[str]:
    """The monogram a newly created object is known by, when it has one."""
    prefixes = {"task": "T", "paste": "P"}
    prefix = prefixes.get(object_type)

    return f"{prefix}{id_}" if prefix and id_ is not None else None


def apply_plan(
    app: Any, plan: CreatePlan, *, pace: Any = None
) -> Iterator[CreateRecord]:
    """Create everything the plan describes, in order, one record at a time.

    A generator that **never raises because the server refused something**.
    Conduit has no transactions, so a spec that fails halfway through has
    left real objects behind, and a generator that raised would lose exactly
    the records saying which. On the first failure it yields the `failed`
    record, then a `skipped` record for every item left, and returns.

    Never retried as an idempotent write, and never inside
    `phabfive.retry.idempotent_writes`: a create repeated after a read
    timeout is a second object.

    Parameters
    ----------
    app : Phabfive
        An already-constructed app.
    plan : CreatePlan
    pace : Pacer, optional
        The pause between writes; `PHAB_PACE`'s by default, which is what
        `edit_tasks_batch` and `Edit.apply_all` already use.
    """
    from phabfive.retry import Pacer

    if pace is None:
        pace = Pacer.from_conf(getattr(app, "conf", None) or {})

    realised: dict[str, str] = {}
    paths: dict[str, tuple[str, ...]] = {}
    failed = False

    for item in plan.items:
        if failed:
            # Before the anchor branch, and that order is the point. An
            # anchor whose `phids` hold a `$local-id` whose object failed
            # would otherwise reach `_realise`, which raises straight out of
            # this generator - losing every record, including the objects
            # that really were created, which is exactly what #485 exists to
            # prevent. Nothing is read and nothing is sent once a run has
            # stopped.
            if item.anchor:
                continue

            yield CreateRecord(
                object_type=item.object_type,
                path=item.path,
                status="skipped",
                local_id=item.local_id,
                title=item.title,
                reason="an earlier object could not be created",
            )
            continue

        if item.anchor:
            # Read for its PHIDs, never written: no `edit` call is made for
            # it at all, which is what makes anchoring to an existing object
            # safe. It is in the plan so the dry run shows what its children
            # hang off, and so the ordering has something to point at.
            if item.phids:
                anchored = tuple(_realise(one, realised) for one in item.phids)
                paths[item.path] = anchored

                if item.local_id and len(anchored) == 1:
                    # Only when it names exactly one object: a `$ref` to an
                    # anchor is a reference to one thing, and there is no
                    # honest answer for an anchor that named two.
                    realised[item.local_id] = anchored[0]

            continue

        pace.wait()

        try:
            record = apply_item(app, item, realised, paths)
        except CreatePlanError:
            # A `.set` in a plan, or a `$ref` naming nothing this run
            # created: both are planner bugs rather than the server
            # refusing something, and a bug reported as a per-object
            # failure is a bug nobody notices. It comes straight out.
            raise
        except Exception as error:  # noqa: BLE001 - re-reported, not swallowed
            failed = True
            yield CreateRecord(
                object_type=item.object_type,
                path=item.path,
                status="failed",
                local_id=item.local_id,
                title=item.title,
                reason=str(error),
                error=error,
            )
            continue

        if record.phid:
            paths[item.path] = (record.phid,)

            if item.local_id:
                realised[item.local_id] = record.phid

        yield record


def apply_spec(app: Any, plan: CreatePlan, *, pace: Any = None) -> CreateReport:
    """Every record of one run, for a caller that does not watch it happen."""
    return CreateReport(records=tuple(apply_plan(app, plan, pace=pace)))
