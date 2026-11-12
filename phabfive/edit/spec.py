# -*- coding: utf-8 -*-
"""Planning and applying an edit spec: objects that exist, made what a file says.

An edit document (``kind: edit``, see `phabfive.spec.edit`) names objects and
states what each should be. `plan_edit` reads every one of them as the
instance has it, works out the difference, and changes nothing;
`apply_edit_plan` sends what the plan holds. Neither prints, prompts or reads
a terminal - the command is `phabfive apply -f`.

No transaction is built here. Each key of an item is handed to the engine
`phabfive edit` already uses - `Maniphest.build_task_edit` - which compares
it with the task and answers with the transactions that close the gap, or
none. What this module adds is the translation from *state* to that engine's
*add and remove*: a list that is the full set becomes "add these, remove
whatever else is there".

Three properties follow, and the tests hold each:

- **A document that matches the instance plans nothing.** Exporting an
  object and applying the file unchanged sends no transaction.
- **Applying twice is applying once.** The second run finds nothing left to
  change. That includes a ``+``/``-`` list: an entry already added is not
  added again.
- **The stamp guards a change, not a look.** An item's ``modified:`` is
  compared with the object's only when the item would change something, so
  the run that follows a successful one is not refused for the modification
  it made itself.

Every item is planned before any is applied. A name that does not resolve, a
task that does not exist, a stale stamp - each is a `Problem`, all of them
are reported together, and a plan holding one applies nothing.
"""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Callable, Optional

from phabfive.exceptions import (
    PhabfiveException,
    PhabfiveInputException,
    PhabfiveNotFoundException,
    PhabfiveRemoteException,
)
from phabfive.spec.edit import (
    EDIT_OBJECT_KEYS,
    IDENTITY_KEYS,
    STAMP_KEY,
    ListEdit,
    format_stamp,
    parse_list_edit,
    parse_stamp,
)
from phabfive.spec.envelope import Kind
from phabfive.spec.problems import Layer, Problem, Severity

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from phabfive.spec.envelope import Spec

__all__ = [
    "EditItem",
    "EditPlanError",
    "EditRecord",
    "EditReport",
    "EditSpecPlan",
    "apply_edit_plan",
    "apply_edit_spec",
    "plan_edit",
]


class EditPlanError(PhabfiveInputException):
    """A spec that cannot be planned as an edit at all.

    Not a problem with one item, which is a `Problem` in the plan: this is a
    spec of another kind, or one the offline layer has errors for. `problems`
    holds those errors when that is the reason.
    """

    def __init__(self, message: str, *, problems: tuple[Problem, ...] = ()):
        super().__init__(message)
        self.problems = problems


@dataclasses.dataclass(frozen=True)
class EditItem:
    """What one item of the document would do to the object it names.

    Attributes
    ----------
    object_type : str
        "task".
    path : str
        Where the item is in the document, e.g. "tasks[0]".
    monogram : str
        The object, e.g. "T123".
    title : str or None
        What the object is called now, for a line a person reads.
    transactions : tuple
        The Conduit transactions that make the change. Empty when the object
        is already as the item says.
    changes : tuple
        What they change, as {"field", "old", "new"} dicts.
    """

    object_type: str
    path: str
    monogram: str
    title: Optional[str] = None
    transactions: tuple = ()
    changes: tuple = ()

    @property
    def noop(self) -> bool:
        """Whether the object is already as asked, so nothing would be sent."""
        return not self.transactions

    def as_record(self) -> dict[str, Any]:
        return {
            "type": self.object_type,
            "path": self.path,
            "monogram": self.monogram,
            "title": self.title,
            "status": "unchanged" if self.noop else "would-edit",
            "changes": [dict(change) for change in self.changes],
        }


@dataclasses.dataclass(frozen=True)
class EditSpecPlan:
    """Everything an edit document would do, in document order.

    `items` holds the items that could be planned and `problems` what stopped
    the others. A plan with an error in `problems` is not applied - see
    `apply_edit_plan` - because half a document applied is a state nobody
    wrote down.
    """

    items: tuple[EditItem, ...] = ()
    problems: tuple[Problem, ...] = ()

    __hash__ = None  # type: ignore[assignment]

    @property
    def errors(self) -> tuple[Problem, ...]:
        return tuple(
            problem for problem in self.problems if problem.severity == Severity.ERROR
        )

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def edits(self) -> tuple[EditItem, ...]:
        """The items that would change something."""
        return tuple(item for item in self.items if not item.noop)

    def as_records(self) -> list[dict[str, Any]]:
        return [item.as_record() for item in self.items]


@dataclasses.dataclass(frozen=True)
class EditRecord:
    """What became of one item when the plan was applied.

    `status` is "edited", "unchanged", "failed" or "skipped". "unchanged" is
    an object that was already as the item says, for which nothing was sent;
    "skipped" is what every item after a failure gets.
    """

    object_type: str
    path: str
    status: str
    monogram: Optional[str] = None
    title: Optional[str] = None
    changes: tuple = ()
    reason: Optional[str] = None
    error: Optional[BaseException] = dataclasses.field(
        default=None, compare=False, repr=False
    )

    @property
    def label(self) -> str:
        parts = [
            self.object_type,
            self.monogram,
            f"{self.title!r}" if self.title else None,
        ]

        return " ".join(part for part in parts if part)

    def as_record(self) -> dict[str, Any]:
        return {
            "type": self.object_type,
            "path": self.path,
            "status": self.status,
            "monogram": self.monogram,
            "title": self.title,
            "changes": [dict(change) for change in self.changes],
            "reason": self.reason,
        }


@dataclasses.dataclass(frozen=True)
class EditReport:
    """Every record of one run, and whether it finished."""

    records: tuple[EditRecord, ...] = ()

    __hash__ = None  # type: ignore[assignment]

    def _with(self, status: str) -> tuple[EditRecord, ...]:
        return tuple(record for record in self.records if record.status == status)

    @property
    def edited(self) -> tuple[EditRecord, ...]:
        return self._with("edited")

    @property
    def unchanged(self) -> tuple[EditRecord, ...]:
        return self._with("unchanged")

    @property
    def failures(self) -> tuple[EditRecord, ...]:
        return self._with("failed")

    @property
    def skipped(self) -> tuple[EditRecord, ...]:
        return self._with("skipped")

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_records(self) -> list[dict[str, Any]]:
        return [record.as_record() for record in self.records]

    @property
    def summary(self) -> str:
        """One line saying what changed, for a caller that prints one."""
        edited = len(self.edited)
        total = len(self.records)
        unchanged = len(self.unchanged)

        sentence = f"{edited} of {total} objects edited"

        if unchanged:
            sentence += f", {unchanged} already as written"

        failures = self.failures

        if not failures:
            return f"{sentence}."

        sentence += f". {failures[0].label} failed: {failures[0].reason}"
        skipped = len(self.skipped)

        if skipped:
            sentence += f" ({skipped} not attempted)"

        return sentence


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def _online(
    path: str, field: Optional[str], value: Any, reason: str, *, code: str
) -> Problem:
    """One thing the instance could not make of one key of one item."""
    return Problem(
        object=path,
        field=field,
        value=value,
        reason=reason,
        code=code,
        layer=Layer.ONLINE,
        severity=Severity.ERROR,
    )


class _TaskPlanner:
    """One task item, taken key by key through the edit engine.

    Key by key, rather than one call carrying every key, for the report: the
    engine raises on the first value it cannot use, and a document with a
    misspelled status *and* an unknown subscriber should hear about both,
    each under the key that holds it. The keys the engine reads together -
    a column and the projects it may ride on, parents and subtasks - are
    still sent together.
    """

    def __init__(self, maniphest: Any, path: str, item: Mapping[str, Any]):
        self.maniphest = maniphest
        self.path = path
        self.item = item
        self.monogram = str(item[IDENTITY_KEYS["task"]]).strip().upper()
        self.task_id = self.monogram[1:]
        self.data: dict[str, Any] = {}
        self.transactions: list = []
        self.changes: list = []
        self.problems: list[Problem] = []

    def _given(self, key: str) -> bool:
        """A key that is absent, or written with nothing after it, is not given."""
        return self.item.get(key) is not None

    def _refuse(self, key: str, error: BaseException, *, code: str) -> None:
        self.problems.append(
            _online(self.path, key, self.item.get(key), str(error), code=code)
        )

    def _attempt(self, key: str, call: Callable[[], Any], *, code: str) -> Any:
        """Run one lookup, turning what the instance refuses into a problem.

        A request that never landed is not the document's fault and is not
        caught: `PhabfiveRemoteException` goes to the caller, which reports
        that the instance could not be asked.
        """
        try:
            return call()
        except PhabfiveRemoteException:
            raise
        except PhabfiveException as error:
            self._refuse(key, error, code=code)
            return None

    def _engine(self, key: str, *, code: str, **arguments: Any) -> None:
        """Ask the edit engine for one key's transactions.

        `code` is what a refusal is reported under: the key's own kind of
        unknown where the vocabulary has one.
        """

        def call() -> None:
            transactions, changes = self.maniphest.build_task_edit(
                self.task_id, self.data, **arguments
            )
            self.transactions.extend(transactions)
            self.changes.extend(changes)

        self._attempt(key, call, code=code)

    def _list(
        self,
        key: str,
        current: Callable[[], list],
        resolve: Callable[[list], Mapping[str, tuple]],
        *,
        code: str,
    ) -> Optional[tuple[Mapping[str, tuple], Any]]:
        """What a list-valued key adds and removes, resolved.

        Returns
        -------
        (added, removed), or None when the key was refused
            `added` is what the resolver answered for the names to add.
            `removed` is the same for a delta, and for a full set the PHIDs
            on the object now that the set does not hold.
        """
        edit: ListEdit = parse_list_edit(self.item[key])

        def resolved() -> tuple[Mapping[str, tuple], Any]:
            if edit.delta:
                return (
                    resolve(list(edit.added)) if edit.added else {},
                    resolve(list(edit.removed)) if edit.removed else {},
                )

            wanted = resolve(list(edit.values)) if edit.values else {}
            keep = {phid for phid, _ in wanted.values()}

            return wanted, [phid for phid in current() if phid not in keep]

        return self._attempt(key, resolved, code=code)

    def _subscribers(self, values: list) -> Mapping[str, tuple]:
        """Subscribers, resolved: users by name, anything else by its PHID.

        A task's subscribers are not all users - a project can subscribe,
        and so can a package - and an export writes those as the PHID they
        are. They pass through here so a full set naming one keeps it;
        `_subscribe` is what refuses to add or remove one, which only a
        user can be.
        """
        from phabfive.users import USER_PHID_PREFIX

        others = [
            value
            for value in values
            if value.startswith("PHID-") and not value.startswith(USER_PHID_PREFIX)
        ]
        users = [value for value in values if value not in others]
        resolved = self.maniphest._resolve_users(users) if users else {}

        return {
            value: resolved[value] if value in resolved else (value, value)
            for value in values
        }

    def _subscribe(self, subscribers: tuple, current: list) -> None:
        """Hand the engine the users to add and remove, and only users."""
        from phabfive.users import USER_PHID_PREFIX

        added, removed = subscribers
        adding = [value for value, (phid, _) in added.items() if phid not in current]
        removing = (
            [value for value, (phid, _) in removed.items() if phid in current]
            if isinstance(removed, Mapping)
            else list(removed)
        )

        def phid_of(value: str) -> str:
            for mapping in (added, removed if isinstance(removed, Mapping) else {}):
                if value in mapping:
                    return mapping[value][0]
            return value

        strangers = [
            value
            for value in adding + removing
            if not phid_of(value).startswith(USER_PHID_PREFIX)
        ]

        if strangers:
            self._refuse(
                "subscribers",
                PhabfiveInputException(
                    f"{', '.join(strangers)} is not a user. A spec subscribes "
                    f"and unsubscribes users; a project or package that "
                    f"subscribes is changed in the web UI"
                ),
                code="unknown-user",
            )
            return

        if adding or removing:
            self._engine(
                "subscribers",
                code="unknown-user",
                subscribe=adding,
                unsubscribe=removing,
            )

    def _attached(self, name: str, key: str) -> list:
        return list(self.data.get("attachments", {}).get(name, {}).get(key, []))

    def _edges(self, kind: str) -> list:
        from phabfive.maniphest.fetchers import fetch_task_edges

        return list(fetch_task_edges(self.maniphest.phab, self.data["phid"], kind))

    def _boards(self, added: Mapping[str, tuple], removed: Any) -> Optional[list]:
        """The one board a `column:` is on, or None with the problem reported.

        A column is a place on a board, and a task has one position per
        board it is on. The item names no board, so there has to be exactly
        one it could mean: the board the task is on, or - for a task on
        none - the one project this item tags it into.
        """
        gone = (
            {phid for phid, _ in removed.values()}
            if isinstance(removed, Mapping)
            else set(removed or [])
        )
        on = [
            phid
            for phid in self.data.get("attachments", {})
            .get("columns", {})
            .get("boards", {})
            if phid not in gone
        ]

        if len(on) == 1:
            return on

        if not on:
            tagged = list(dict.fromkeys(phid for phid, _ in added.values()))

            if len(tagged) == 1:
                return tagged

            self._refuse(
                "column",
                PhabfiveInputException(
                    f"{self.monogram} is on no workboard once this item is "
                    f"applied, so there is no board for the column to be on. "
                    f"Remove column:, or leave the task tagged into exactly "
                    f"one project with a workboard"
                ),
                code="unknown-column",
            )
            return None

        self._refuse(
            "column",
            PhabfiveInputException(
                f"{self.monogram} is on {len(on)} workboards, and column: "
                f"names a column without saying which board. Remove column:, "
                f"and move it with phabfive edit {self.monogram} --tag BOARD "
                f"--column COLUMN"
            ),
            code="unknown-column",
        )
        return None

    def plan(self) -> None:
        maniphest = self.maniphest
        item = self.item

        try:
            self.data = maniphest._get_task_data(self.task_id)
        except PhabfiveNotFoundException as error:
            self.problems.append(
                _online(
                    self.path,
                    IDENTITY_KEYS["task"],
                    item.get(IDENTITY_KEYS["task"]),
                    str(error),
                    code="unknown-reference",
                )
            )
            return

        if self._given("title"):
            self._engine("title", code="unknown-reference", title=item["title"])

        if self._given("priority"):
            self._engine("priority", code="unknown-priority", priority=item["priority"])

        if self._given("status"):
            self._engine("status", code="unknown-status", status=item["status"])

        # Projects and the column together: a task moved onto a board goes
        # there in the same projects.add as a tag, and the engine refuses a
        # move on a board the same edit takes the task off
        tags: Any = ({}, {})

        if self._given("projects"):
            tags = self._list(
                "projects",
                lambda: self._attached("projects", "projectPHIDs"),
                lambda values: maniphest._resolve_project_tags(values),
                code="unknown-project",
            )

        if tags is not None:
            added, removed = tags
            arguments: dict[str, Any] = {"tag": added, "untag": removed}
            ready = True

            if self._given("column"):
                boards = self._boards(added, removed)

                if boards is None:
                    ready = False
                else:
                    arguments.update(column=item["column"], board_phids=boards)

            if ready and "column" in arguments:
                # The projects resolved above, so what is refused here is the
                # column: one the board does not have
                self._engine("column", code="unknown-column", **arguments)
            elif ready and (added or removed):
                self._engine("projects", code="unknown-project", **arguments)

        if self._given("assignment"):
            self._engine("assignment", code="unknown-user", assign=item["assignment"])

        if self._given("description"):
            self._engine(
                "description", code="unknown-reference", description=item["description"]
            )

        if self._given("subscribers"):
            current = self._attached("subscribers", "subscriberPHIDs")
            subscribers = self._list(
                "subscribers",
                lambda: current,
                self._subscribers,
                code="unknown-user",
            )

            if subscribers is not None:
                self._subscribe(subscribers, current)

        if self._given("commits"):
            from phabfive.commits import resolve_commit_phids

            commits = self._list(
                "commits",
                lambda: self._edges("commits"),
                lambda values: resolve_commit_phids(maniphest.phab, values),
                code="unknown-commit",
            )

            if commits is not None:
                added, removed = commits
                self._engine(
                    "commits",
                    code="unknown-commit",
                    attach=list(added),
                    detach=list(removed),
                )

        # Parents and subtasks together, so a task named as both is refused
        # by the check that sees both
        relations: dict[str, Any] = {}

        for key, add, remove in (
            ("parents", "parent", "unparent"),
            ("subtasks", "subtask", "unsubtask"),
        ):
            if not self._given(key):
                continue

            linked = self._list(
                key,
                functools.partial(self._edges, key),
                maniphest._task_refs,
                code="unknown-reference",
            )

            if linked is None:
                relations = {}
                break

            added, removed = linked
            relations[add] = added
            # A full set's removals are PHIDs, which the engine looks up for
            # the monogram a preview names them by
            relations[remove] = removed

        if any(relations.values()):
            self._engine(
                "parents" if "parent" in relations else "subtasks",
                code="unknown-reference",
                **{name: value for name, value in relations.items() if value},
            )

        if self._given("space"):
            self._engine("space", code="unknown-space", space=item["space"])

        policies = {
            argument: item[key]
            for key, argument in (
                ("visible-to", "visible_to"),
                ("editable-by", "editable_by"),
            )
            if self._given(key)
        }

        if policies:
            self._engine(
                "visible-to" if "visible_to" in policies else "editable-by",
                code="unknown-reference",
                **policies,
            )

    def stale(self) -> Optional[Problem]:
        """The problem of a change to a task modified since the stamp.

        Only asked of an item that would change something. A stamp older
        than the task is the case; a stamp *newer* than it cannot be read as
        anybody else's change, so it is let through.
        """
        if not self._given(STAMP_KEY):
            return None

        stamp = parse_stamp(self.item[STAMP_KEY])
        modified = int(self.data.get("fields", {}).get("dateModified") or 0)

        if modified <= stamp:
            return None

        return _online(
            self.path,
            STAMP_KEY,
            self.item[STAMP_KEY],
            (
                f"{self.monogram} was modified at {format_stamp(modified)}, "
                f"after the {format_stamp(stamp)} this item was written "
                f"against, and the item would change it. Write it against the "
                f"task as it is now, or apply with --force to overwrite"
            ),
            code="stale",
        )


def plan_edit(app: Any, spec: "Spec", *, force: bool = False) -> EditSpecPlan:
    """Everything an edit document would change, and change nothing.

    Parameters
    ----------
    app : Phabfive
        An already-constructed app. A `Maniphest`, or anything it can be
        built from as a sibling sharing the client.
    spec : Spec
        A ``kind: edit`` spec, rendered if it uses variables.
    force : bool, optional
        Plan a change to an object modified since the item's ``modified:``
        instead of reporting it as ``stale``.

    Returns
    -------
    EditSpecPlan
        The planned items and every problem, in document order.

    Raises
    ------
    EditPlanError
        The spec is not an edit spec, or the offline layer has errors for
        it - which are on the exception's `problems`.
    PhabfiveRemoteException
        The instance could not be asked.
    """
    from phabfive.create.dispatch import app_for

    if spec.kind is not Kind.EDIT:
        raise EditPlanError(
            f"{spec.source} is a {spec.kind.value} spec, not an edit spec"
        )

    offline = tuple(
        problem
        for problem in spec.validate_offline()
        if problem.severity == Severity.ERROR
    )

    if offline:
        raise EditPlanError(
            f"{spec.source} has {len(offline)} offline problem(s): {offline[0].reason}",
            problems=offline,
        )

    maniphest = app_for("task", app)

    items: list[EditItem] = []
    problems: list[Problem] = []

    for key, object_type in EDIT_OBJECT_KEYS:
        for index, item in enumerate(spec.items(key)):
            planner = _TaskPlanner(maniphest, f"{key}[{index}]", item)
            planner.plan()

            if not planner.problems and planner.transactions and not force:
                stale = planner.stale()

                if stale is not None:
                    planner.problems.append(stale)

            problems.extend(planner.problems)

            if planner.problems:
                continue

            items.append(
                EditItem(
                    object_type=object_type,
                    path=planner.path,
                    monogram=planner.monogram,
                    title=planner.data.get("fields", {}).get("name"),
                    transactions=tuple(planner.transactions),
                    changes=tuple(planner.changes),
                )
            )

    return EditSpecPlan(items=tuple(items), problems=tuple(problems))


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def apply_edit_plan(
    app: Any, plan: EditSpecPlan, *, pace: Any = None
) -> Iterator[EditRecord]:
    """Send what the plan holds, in order, one record at a time.

    A generator that never raises because the server refused an edit.
    Conduit has no transactions, so a run that fails halfway has changed
    real objects, and the records saying which are the point: on the first
    failure it yields the `failed` record, then `skipped` for every item
    left, and returns.

    Rerunning the same document afterwards is safe: the items that landed
    are found unchanged, and the stamp is not asked about an item with
    nothing left to change.

    Raises
    ------
    EditPlanError
        The plan holds an error. It is refused whole rather than applied in
        part.
    """
    from phabfive.create.dispatch import app_for
    from phabfive.retry import Pacer

    if not plan.ok:
        raise EditPlanError(
            f"The plan has {len(plan.errors)} problem(s) and is not applied: "
            f"{plan.errors[0].reason}",
            problems=plan.errors,
        )

    maniphest = app_for("task", app)

    if pace is None:
        pace = Pacer.from_conf(getattr(maniphest, "conf", None) or {})

    failed = False

    for item in plan.items:
        common: dict[str, Any] = {
            "object_type": item.object_type,
            "path": item.path,
            "monogram": item.monogram,
            "title": item.title,
        }

        if failed:
            yield EditRecord(
                status="skipped",
                reason="an earlier object could not be edited",
                **common,
            )
            continue

        if item.noop:
            yield EditRecord(status="unchanged", **common)
            continue

        pace.wait()

        try:
            maniphest.apply_task_edit(item.monogram[1:], list(item.transactions))
        except Exception as error:  # noqa: BLE001 - re-reported, not swallowed
            failed = True
            yield EditRecord(
                status="failed",
                changes=item.changes,
                reason=str(error),
                error=error,
                **common,
            )
            continue

        yield EditRecord(status="edited", changes=item.changes, **common)


def apply_edit_spec(app: Any, plan: EditSpecPlan, *, pace: Any = None) -> EditReport:
    """Every record of one run, for a caller that does not watch it happen."""
    return EditReport(records=tuple(apply_edit_plan(app, plan, pace=pace)))
