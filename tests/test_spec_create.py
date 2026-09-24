# -*- coding: utf-8 -*-

"""CreatePlan: what a create spec would create, before anything is written (#480).

`create_tasks_from_config` used to be four hundred lines doing parsing,
rendering, resolution, transaction building, committing and dry-run display
in one recursion. It is `phabfive.spec.create` now: `plan_create` answers
with a frozen, inspectable `CreatePlan` and `apply_plan` is a separate step
that yields a record per object.

What is pinned here is what the plan *is* - inspectable and serializable
without applying it, resolved atomically, ordered, and incapable of sending
a transaction that could discard what an existing object already holds.
`tests/test_create_port_parity.py` pins that it still creates what the old
recursion created.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.maniphest.core import Maniphest
from phabfive.spec import Spec
from phabfive.spec.create import (
    EDIT_ENDPOINTS,
    CreateItem,
    CreatePlan,
    CreatePlanError,
    CreateRecord,
    apply_item,
    apply_plan,
    apply_spec,
    plan_create,
    substitute,
)

PROJECTS = {
    "PHID-PROJ-backend": {"name": "Backend Team", "slugs": ["backend"]},
    "PHID-PROJ-sprint": {"name": "Sprint 42", "slugs": []},
}

USERS = {"alice": "PHID-USER-alice", "bob": "PHID-USER-bob"}


def _phab():
    """A client that knows two projects, two users and hands out task IDs."""
    phab = MagicMock()
    phab.project.query.return_value = {"data": PROJECTS}
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller", "userName": "caller"}

    def user_search(constraints):
        names = {n.casefold() for n in constraints.get("usernames", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                {"phid": phid, "fields": {"username": name}}
                for name, phid in USERS.items()
                if name in names or phid in phids
            ]
        }

    phab.user.search.side_effect = user_search

    ids = iter(range(1, 100))

    def edit(transactions):
        task_id = next(ids)
        return {"object": {"id": task_id, "phid": f"PHID-TASK-{task_id}"}}

    phab.maniphest.edit.side_effect = edit
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


def _spec(body):
    return Spec.from_data(body, kind="create").render()


def _plan(phab, body):
    return plan_create(_maniphest(phab), _spec(body))


def _task(title="Task", **fields):
    return {"title": title, "description": "x", **fields}


class TestThePlanIsData:
    """It is frozen, inspectable and serializable, and nothing was written."""

    def test_planning_writes_nothing(self):
        phab = _phab()

        _plan(phab, {"tasks": [_task(), _task("Second")]})

        phab.maniphest.edit.assert_not_called()

    def test_it_holds_one_item_per_task_in_document_order(self):
        phab = _phab()

        plan = _plan(
            phab,
            {"tasks": [_task("Parent", tasks=[_task("Child")]), _task("Second")]},
        )

        assert [item.path for item in plan.items] == [
            "tasks[0]",
            "tasks[0].tasks[0]",
            "tasks[1]",
        ]
        assert [item.display["title"] for item in plan.items] == [
            "Parent",
            "Child",
            "Second",
        ]

    def test_a_nested_task_carries_its_depth_and_its_parent(self):
        phab = _phab()

        plan = _plan(
            phab,
            {
                "tasks": [
                    _task("Parent", tasks=[_task("Child", tasks=[_task("Grand")])])
                ]
            },
        )

        assert [(item.depth, item.parent_path) for item in plan.items] == [
            (0, None),
            (1, "tasks[0]"),
            (2, "tasks[0].tasks[0]"),
        ]

    def test_it_survives_json(self):
        """A frontend holds a plan, and a plan goes over the wire as records."""
        phab = _phab()

        plan = _plan(
            phab,
            {"tasks": [_task("Parent", projects=["Backend Team"], tasks=[_task("C")])]},
        )

        records = json.loads(json.dumps(plan.as_records()))

        assert [record["path"] for record in records] == [
            "tasks[0]",
            "tasks[0].tasks[0]",
        ]
        assert {"type": "projects.add", "value": ["PHID-PROJ-backend"]} in (
            records[0]["transactions"]
        )

    def test_it_counts_what_it_would_create(self):
        phab = _phab()

        plan = _plan(phab, {"tasks": [_task(), _task("Second", tasks=[_task("C")])]})

        assert plan.counts() == {"task": 3}
        assert len(plan) == 3

    def test_an_item_is_not_hashable(self):
        """`display` is a mapping, so a promise of hashability is one it cannot keep."""
        with pytest.raises(TypeError):
            {CreateItem(object_type="task", path="tasks[0]")}

        with pytest.raises(TypeError):
            {CreatePlan()}

    def test_a_declared_id_is_how_an_item_is_found(self):
        phab = _phab()

        plan = _plan(phab, {"tasks": [_task("Epic", id="epic")]})

        assert plan.by_local_id()["epic"].display["title"] == "Epic"
        assert plan.items_of("task") == plan.items
        assert plan.items_of("paste") == ()


class TestEverythingIsResolvedFirst:
    def test_every_bad_reference_is_reported_in_one_run(self):
        """The acceptance criterion: one run, every problem."""
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(
                phab,
                {
                    "tasks": [
                        _task("One", assignment="nosuch"),
                        _task("Two", projects=["Nowhere"]),
                        _task("Three", subscribers=["alice", "alsonosuch"]),
                    ]
                },
            )

        message = str(excinfo.value)

        assert "3 problem(s)" in message
        assert "tasks[0].assignment: No such user: 'nosuch'" in message
        assert "tasks[1].projects[0]: No such project: 'Nowhere'" in message
        assert "tasks[2].subscribers[1]: No such user: 'alsonosuch'" in message
        phab.maniphest.edit.assert_not_called()

    def test_one_name_in_forty_tasks_is_resolved_once(self):
        phab = _phab()

        _plan(phab, {"tasks": [_task(f"T{n}", assignment="alice") for n in range(40)]})

        assert phab.user.search.call_count == 1
        assert phab.project.query.call_count == 0

    def test_a_spec_naming_nobody_asks_about_nobody(self):
        phab = _phab()

        _plan(phab, {"tasks": [_task()]})

        phab.user.search.assert_not_called()
        phab.project.query.assert_not_called()

    def test_a_shape_problem_costs_no_request_at_all(self):
        """A priority that is not one is refused before anything is fetched."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="hgih"):
            _plan(phab, {"tasks": [_task(priority="hgih", projects=["Backend Team"])]})

        phab.project.query.assert_not_called()
        phab.user.search.assert_not_called()


class TestTitleAndDescription:
    """The deliberate behaviour change of #480."""

    def test_a_task_without_a_description_is_created(self):
        """It used to be dropped with a log.warning nobody reads."""
        phab = _phab()

        plan = _plan(phab, {"tasks": [{"title": "Just a title"}]})

        assert plan.counts() == {"task": 1}
        assert {"type": "title", "value": "Just a title"} in (
            plan.items[0].transactions
        )
        assert all(one["type"] != "description" for one in plan.items[0].transactions)

    def test_a_task_with_neither_a_title_nor_children_is_refused(self):
        """It used to vanish, and the template created one task fewer."""
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(phab, {"tasks": [_task("Real"), {"description": "orphan"}]})

        message = str(excinfo.value)

        assert "tasks[1].title" in message
        assert "needs a title" in message
        phab.maniphest.edit.assert_not_called()

    def test_a_titleless_container_holding_children_is_legal(self):
        """A bare grouping creates nothing itself and is not an error."""
        phab = _phab()

        plan = _plan(phab, {"tasks": [{"tasks": [_task("Child")]}]})

        assert plan.items[0].anchor is True
        assert plan.items[0].transactions == ()
        assert plan.counts() == {"task": 1}


class TestNothingCanDiscardWhatAnObjectHas:
    """Every collection field is `.add`; `.set` is refused at the choke point."""

    @pytest.mark.parametrize(
        "field, value, expected",
        [
            ("projects", ["Backend Team"], "projects.add"),
            ("subscribers", ["alice"], "subscribers.add"),
            ("parents", ["T7"], "parents.add"),
            ("subtasks", ["T7"], "subtasks.add"),
        ],
    )
    def test_a_collection_is_filled_with_add(self, field, value, expected):
        phab = _phab()
        phab.maniphest.search.return_value = {
            "data": [{"id": 7, "phid": "PHID-TASK-7"}]
        }

        plan = _plan(phab, {"tasks": [_task(**{field: value})]})

        types = [one["type"] for one in plan.items[0].transactions]

        assert expected in types
        assert not any(one.endswith(".set") for one in types)

    def test_apply_refuses_to_send_a_set(self):
        """A guard at the choke point, not only a convention in the builder."""
        phab = _phab()
        item = CreateItem(
            object_type="task",
            path="tasks[0]",
            transactions=({"type": "subtasks.set", "value": ["PHID-TASK-1"]},),
        )

        with pytest.raises(CreatePlanError) as excinfo:
            apply_item(_maniphest(phab), item, {}, {})

        assert excinfo.value.check == "unsafe-transaction"
        phab.maniphest.edit.assert_not_called()

    def test_apply_refuses_to_send_a_remove(self):
        phab = _phab()
        item = CreateItem(
            object_type="task",
            path="tasks[0]",
            transactions=({"type": "projects.remove", "value": ["PHID-PROJ-x"]},),
        )

        with pytest.raises(CreatePlanError):
            apply_item(_maniphest(phab), item, {}, {})

        phab.maniphest.edit.assert_not_called()

    def test_nothing_is_ever_sent_with_an_object_identifier(self):
        """A create spec has no code path that edits an existing object."""
        phab = _phab()

        plan = _plan(phab, {"tasks": [_task("Parent", tasks=[_task("Child")])]})
        list(apply_plan(_maniphest(phab), plan))

        assert phab.maniphest.edit.call_count == 2
        for call in phab.maniphest.edit.call_args_list:
            assert "objectIdentifier" not in call.kwargs
            assert call.args == ()


class TestOrder:
    def test_a_local_reference_is_created_before_the_item_naming_it(self):
        phab = _phab()

        plan = _plan(
            phab,
            {
                "tasks": [
                    _task("Needs the epic", parents=["$epic"]),
                    _task("Epic", id="epic"),
                ]
            },
        )

        assert [item.display["title"] for item in plan.items] == [
            "Epic",
            "Needs the epic",
        ]

    def test_a_local_reference_stays_literal_in_the_plan(self):
        """It names something that does not exist yet, so there is no PHID."""
        phab = _phab()

        plan = _plan(
            phab,
            {"tasks": [_task("Epic", id="epic"), _task("Child", parents=["$epic"])]},
        )

        child = plan.by_path()["tasks[1]"]

        assert {"type": "parents.add", "value": ["$epic"]} in child.transactions

        # `depends_on` is item paths, the same identity `path` and
        # `parent_path` hold - not the `$ref` the value was written as. One
        # vocabulary, so `as_record()` hands a frontend one kind of name,
        # and so an item a `$ref` names but that has no `id:` of its own is
        # still something to depend on.
        assert child.depends_on == ("tasks[0]",)

    def test_apply_substitutes_it_with_what_was_created(self):
        phab = _phab()

        plan = _plan(
            phab,
            {"tasks": [_task("Epic", id="epic"), _task("Child", parents=["$epic"])]},
        )
        records = list(apply_plan(_maniphest(phab), plan))

        assert [record.monogram for record in records] == ["T1", "T2"]

        sent = phab.maniphest.edit.call_args_list[1].kwargs["transactions"]
        assert {"type": "parents.add", "value": ["PHID-TASK-1"]} in sent

    def test_substitute_refuses_a_name_nothing_created(self):
        """A planner bug, refused rather than sent as the literal `$x`."""
        with pytest.raises(PhabfiveInputException) as excinfo:
            substitute([{"type": "parents.add", "value": ["$nope"]}], {})

        assert excinfo.value.check == "unresolved-local-id"


class TestApply:
    def test_it_yields_a_record_per_object(self):
        phab = _phab()

        plan = _plan(phab, {"tasks": [_task("One"), _task("Two")]})
        report = apply_spec(_maniphest(phab), plan)

        assert report.ok is True
        assert report.task_ids == [1, 2]
        assert [record.as_record()["status"] for record in report.records] == [
            "created",
            "created",
        ]
        assert report.records[0].as_record() == {
            "local_id": None,
            "type": "task",
            "path": "tasks[0]",
            "status": "created",
            "phid": "PHID-TASK-1",
            "id": 1,
            "monogram": "T1",
            "title": "One",
            "reason": None,
        }

    def test_a_failure_halfway_leaves_a_record_for_everything(self):
        """A generator that raised would lose exactly the records that matter."""
        phab = _phab()
        answers = [
            {"object": {"id": 1, "phid": "PHID-TASK-1"}},
            RuntimeError("ERR-CONDUIT-CORE: nope"),
            {"object": {"id": 3, "phid": "PHID-TASK-3"}},
        ]

        def edit(transactions):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        phab.maniphest.edit.side_effect = edit

        plan = _plan(phab, {"tasks": [_task("One"), _task("Two"), _task("Three")]})
        report = apply_spec(_maniphest(phab), plan)

        assert [record.status for record in report.records] == [
            "created",
            "failed",
            "skipped",
        ]
        assert report.ok is False
        assert report.failures[0].reason == "ERR-CONDUIT-CORE: nope"
        assert report.skipped[0].title == "Three"
        # The third task was never attempted, so nothing exists for it
        assert phab.maniphest.edit.call_count == 2

    def test_a_record_carries_the_exception_but_the_report_does_not(self):
        record = CreateRecord(
            object_type="task", path="tasks[0]", status="failed", error=ValueError("x")
        )

        assert isinstance(record.error, ValueError)
        assert "error" not in record.as_record()


class TestRefusals:
    def test_a_search_spec_is_not_a_create_spec(self):
        spec = Spec.from_data({"kind": "search", "searches": [{"search": {}}]})

        with pytest.raises(CreatePlanError) as excinfo:
            plan_create(_maniphest(_phab()), spec)

        assert excinfo.value.check == "unsupported-kind"

    def test_an_object_type_nothing_creates_yet_is_refused_by_name(self, monkeypatch):
        """The guard between a new section and its builder.

        Every type `CREATE_OBJECT_KEYS` walks has a builder today - tasks
        and projects since #483, pastes since #481 - so the refusal has no
        live case and has to be provoked to be covered. That is the point
        of it: the day a section is added ahead of its builder, a spec
        naming it is refused by name rather than planned into a create
        that leaves the section's fields on the floor.

        `passphrases:` is refused by a different mechanism and never
        reaches here: `phabfive.spec.references.UNCREATABLE_OBJECT_KEYS`
        answers it offline, because no token is needed to know that Phorge
        publishes no `passphrase.edit`.
        """
        monkeypatch.setattr(
            "phabfive.spec.create.CREATABLE_TYPES", frozenset({"task", "project"})
        )
        spec = Spec.from_data({"kind": "create", "pastes": [{"title": "Notes"}]})

        with pytest.raises(CreatePlanError) as excinfo:
            plan_create(_maniphest(_phab()), spec)

        assert excinfo.value.check == "unsupported-type"
        assert excinfo.value.object_type == "paste"
        assert "pastes[0]" in str(excinfo.value)

    def test_a_plan_error_is_an_input_exception(self):
        """So every handler that answers a bad argument value answers this too."""
        assert issubclass(CreatePlanError, PhabfiveInputException)
        assert issubclass(CreatePlanError, ValueError)


class TestValidateOnlyDecidesTheChecks:
    """`validate=False` turns off the checks, and nothing else.

    It used to turn off the online pass with them, which meant every
    reference-valued key came back unanswered and was left out: a task
    written with `projects:`, `assignment:` and `subscribers:` planned down
    to a title and a priority, with no problem, no warning and no exception,
    and `apply_plan` would happily write the stripped version. The
    docstring told a library caller to validate itself and pass
    `validate=False`, so following the advice was what produced bare
    objects.
    """

    BODY = {
        "kind": "create",
        "tasks": [
            {
                "title": "Bootstrap",
                "projects": ["#backend"],
                "assignment": "alice",
                "subscribers": ["bob"],
            }
        ],
    }

    def _types(self, **kwargs):
        plan = plan_create(_maniphest(_phab()), _spec(self.BODY), **kwargs)

        return [one["type"] for one in plan.items[0].transactions]

    def test_the_references_are_there_with_the_checks_on(self):
        assert self._types() == [
            "title",
            "priority",
            "owner",
            "projects.add",
            "subscribers.add",
        ]

    def test_the_references_are_there_with_the_checks_off(self):
        assert self._types(validate=False) == self._types()

    def test_an_empty_resolution_is_how_a_caller_asks_for_no_lookups(self):
        """Explicitly, and only explicitly: it is a plan of the shape alone."""
        from phabfive.spec.online import Resolution

        phab = _phab()
        plan = plan_create(
            _maniphest(phab), _spec(self.BODY), validate=False, resolution=Resolution()
        )

        assert [one["type"] for one in plan.items[0].transactions] == [
            "title",
            "priority",
        ]
        phab.project.query.assert_not_called()
        phab.user.search.assert_not_called()


class TestTheKeysThatUsedToBeDroppedSilently:
    """`status:`, `column:`, `visible-to:` and `editable-by:` (#481).

    All four were declared in the registry, given help text and checked by
    `spec validate`, and then discarded by the planner - so a spec asking
    for a restricted view policy created a task anyone can see, and one
    asking for `status: resolved` created an open task. Declaring a key and
    dropping it is a stronger false signal than never declaring it.
    """

    STATUSES = {
        "statusMap": {"open": "Open", "resolved": "Resolved"},
        "defaultStatus": "open",
    }

    def _phab_with_statuses(self):
        phab = _phab()
        phab.maniphest.querystatuses.return_value = self.STATUSES
        return phab

    def _types(self, phab, task):
        plan = plan_create(_maniphest(phab), _spec({"kind": "create", "tasks": [task]}))

        return {one["type"]: one["value"] for one in plan.items[0].transactions}

    def test_status_becomes_a_status_transaction(self):
        sent = self._types(self._phab_with_statuses(), _task("Done", status="resolved"))

        assert sent["status"] == "resolved"

    def test_a_status_the_instance_does_not_have_is_refused(self):
        with pytest.raises(PhabfiveConfigException):
            self._types(self._phab_with_statuses(), _task("Done", status="nonsense"))

    def test_the_statuses_are_fetched_once_for_the_whole_document(self):
        phab = self._phab_with_statuses()

        plan_create(
            _maniphest(phab),
            _spec(
                {
                    "kind": "create",
                    "tasks": [
                        _task("One", status="resolved"),
                        _task("Two", status="resolved"),
                        _task("Three", status="open"),
                    ],
                }
            ),
        )

        assert phab.maniphest.querystatuses.call_count == 1

    def test_a_document_with_no_status_asks_nothing(self):
        phab = self._phab_with_statuses()

        self._types(phab, _task("Plain"))

        phab.maniphest.querystatuses.assert_not_called()

    def test_the_policies_become_view_and_edit_transactions(self):
        phab = _phab()
        phab.project.search.return_value = {
            "data": [{"phid": "PHID-PROJ-backend", "fields": {"slug": "backend"}}]
        }

        sent = self._types(
            phab,
            _task("Secret", **{"visible-to": "#backend", "editable-by": "admin"}),
        )

        assert sent["view"] == "PHID-PROJ-backend"
        assert sent["edit"] == "admin"


class TestAColumnIsPlacedInTheSameEdit:
    """`column:` resolves against the boards the task's own `projects:` name.

    `maniphest create --column` creates the task and then sends a second
    `maniphest.edit` carrying `objectIdentifier`; a create spec has no code
    path that may carry one at all, so it places the task in the create edit
    instead. Verified against a real Phorge: `projects.add` and `column` in
    one create edit puts the task on the board in that column.
    """

    COLUMNS = {
        "data": [
            {"phid": "PHID-PCOL-doing", "fields": {"name": "Doing", "sequence": 1}},
            {"phid": "PHID-PCOL-done", "fields": {"name": "Done", "sequence": 2}},
        ]
    }

    def _phab_with_columns(self):
        phab = _phab()
        phab.project.column.search.return_value = self.COLUMNS
        return phab

    def _plan(self, phab, **fields):
        return plan_create(
            _maniphest(phab),
            _spec({"kind": "create", "tasks": [_task("Ticket", **fields)]}),
        )

    def test_the_column_is_a_transaction_of_the_create(self):
        plan = self._plan(
            self._phab_with_columns(), projects=["#backend"], column="Doing"
        )

        assert {"type": "column", "value": ["PHID-PCOL-doing"]} in plan.items[
            0
        ].transactions

    def test_it_comes_after_the_projects_it_needs(self):
        plan = self._plan(
            self._phab_with_columns(), projects=["#backend"], column="Doing"
        )
        types = [one["type"] for one in plan.items[0].transactions]

        assert types.index("projects.add") < types.index("column")

    def test_the_name_is_matched_case_insensitively(self):
        plan = self._plan(
            self._phab_with_columns(), projects=["#backend"], column="doing"
        )

        assert {"type": "column", "value": ["PHID-PCOL-doing"]} in plan.items[
            0
        ].transactions

    def test_a_column_no_board_has_is_refused_before_anything_is_written(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            self._plan(
                self._phab_with_columns(), projects=["#backend"], column="Nowhere"
            )

        assert "Nowhere" in str(excinfo.value)

    def test_a_column_without_projects_is_refused_offline(self):
        """The rule `validate_board_column_context` states for `--column`."""
        problems = _spec(
            {"kind": "create", "tasks": [_task("Ticket", column="Doing")]}
        ).validate_offline()

        assert [one.code for one in problems] == ["missing-required"]
        assert "projects:" in problems[0].reason


class TestNothingInTheCreatePathEverSets:
    """The safety rule of the phase, asserted over what is actually sent.

    A create spec may anchor to an object that already exists, and the one
    thing it must never do is `.set` a collection field on one: a
    `subtasks.set` on T123 silently discards every subtask T123 already
    has. The rule is uniform rather than conditional - every collection is
    `.add` on every object, because on a brand-new object the two are
    identical and uniformity removes the whole class of bug.

    `apply_item` refuses to send a `.set` or a `.remove` at all, which is a
    guard and not only a convention, and it never passes an
    `objectIdentifier`, which is stronger still: there is no code path here
    that can edit an existing object.
    """

    #: Everything the two builders can emit, which is what this pins. A new
    #: transaction type has to be added here deliberately, and the review
    #: that adds it is where somebody asks whether it can discard anything.
    ALLOWED = {
        # task
        "title",
        "description",
        "priority",
        "status",
        "owner",
        "projects.add",
        "column",
        "subscribers.add",
        "space",
        "view",
        "edit",
        "subtasks.add",
        "parents.add",
        # project
        "name",
        "parent",
        "milestone",
        "icon",
        "color",
        "slugs",
        "members.add",
        "join",
    }

    def test_the_module_holds_no_set_or_remove_at_all(self):
        """What the module docstring tells a reviewer to grep for."""
        import re
        from pathlib import Path

        import phabfive.spec.create as module

        source = Path(module.__file__).read_text()
        written = re.findall(r'"(\w+\.(?:set|remove))"', source)

        assert written == []

    def test_every_transaction_a_broad_spec_builds_is_allowed(self):
        phab = _phab()
        phab.project.search.return_value = {"data": []}
        phab.project.column.search.return_value = {
            "data": [{"phid": "PHID-PCOL-doing", "fields": {"name": "Doing"}}]
        }
        phab.maniphest.querystatuses.return_value = {
            "statusMap": {"open": "Open", "resolved": "Resolved"}
        }
        phab.maniphest.search.side_effect = lambda constraints, **kwargs: {
            "data": [{"id": 7, "phid": "PHID-TASK-7"}],
            "cursor": {"after": None},
        }
        phab.project.edit.side_effect = lambda transactions: {
            "object": {"id": 100, "phid": "PHID-PROJ-100"}
        }

        plan = _plan(
            phab,
            {
                "kind": "create",
                "projects": [
                    {
                        "id": "platform",
                        "name": "Platform",
                        "description": "d",
                        "icon": "group",
                        "color": "blue",
                        "slugs": ["platform"],
                        "members": ["alice"],
                        "visible-to": "users",
                        "editable-by": "admin",
                        "joinable-by": "users",
                    }
                ],
                "tasks": [
                    {
                        "parent": "T7",
                        "tasks": [
                            {
                                "title": "Child",
                                "description": "d",
                                "priority": "high",
                                "status": "resolved",
                                "assignment": "alice",
                                "subscribers": ["bob"],
                                "projects": ["$platform", "#backend"],
                                "column": "Doing",
                                "subtasks": ["T7"],
                                "visible-to": "users",
                                "editable-by": "admin",
                            }
                        ],
                    }
                ],
            },
        )

        written = {one["type"] for item in plan.items for one in item.transactions}

        assert written
        assert written <= self.ALLOWED

    def test_applying_never_carries_an_object_identifier(self):
        """Not even for the anchor, which is read and never written."""
        phab = _phab()
        phab.maniphest.search.side_effect = lambda constraints, **kwargs: {
            "data": [{"id": 7, "phid": "PHID-TASK-7"}],
            "cursor": {"after": None},
        }

        plan = _plan(
            phab,
            {
                "kind": "create",
                "tasks": [
                    {"parent": "T7", "tasks": [_task("A"), _task("B")]},
                ],
            },
        )
        list(apply_plan(_maniphest(phab), plan))

        calls = phab.maniphest.edit.call_args_list

        assert len(calls) == 2
        for call in calls:
            assert "objectIdentifier" not in call.kwargs
            assert not call.args
            assert {"type": "parents.add", "value": ["PHID-TASK-7"]} in call.kwargs[
                "transactions"
            ]

    def test_a_set_that_reached_a_plan_is_refused_rather_than_sent(self):
        """The choke point, covering the nesting link appended at apply time."""
        phab = _phab()
        item = CreateItem(
            object_type="task",
            path="tasks[0]",
            transactions=({"type": "subtasks.set", "value": ["PHID-TASK-7"]},),
        )

        with pytest.raises(CreatePlanError) as excinfo:
            apply_item(_maniphest(phab), item, {}, {})

        assert excinfo.value.check == "unsafe-transaction"
        phab.maniphest.edit.assert_not_called()


class TestAColumnDoesNotReportTheSymptom:
    """One problem per mistake, when the projects themselves did not resolve."""

    def test_an_unresolved_project_is_not_also_an_unknown_column(self):
        """The project is what went wrong; the column is downstream of it."""
        phab = _phab()
        phab.project.column.search.return_value = {"data": []}

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(
                phab,
                {
                    "kind": "create",
                    "tasks": [
                        _task("Ticket", projects=["No Such Project"], column="Doing")
                    ],
                },
            )

        assert "1 problem(s)" in str(excinfo.value)
        assert "No Such Project" in str(excinfo.value)
        phab.project.column.search.assert_not_called()

    def test_a_shape_only_plan_asks_about_no_columns_at_all(self):
        """An empty `Resolution()` answers nothing, and that is what it means."""
        from phabfive.spec.online import Resolution

        phab = _phab()

        plan = plan_create(
            _maniphest(phab),
            _spec(
                {
                    "kind": "create",
                    "tasks": [
                        _task("Ticket", projects=["Backend Team"], column="Doing")
                    ],
                }
            ),
            validate=False,
            resolution=Resolution(),
        )

        assert [one["type"] for one in plan.items[0].transactions] == [
            "title",
            "description",
            "priority",
        ]
        phab.project.column.search.assert_not_called()


class TestAPasteIsPlannedLikeEverythingElse:
    """The second half of #481: `pastes:` reaches `paste.edit`.

    A paste is the simplest thing a create spec holds - a leaf with no
    parent, no children and no space - so what is pinned here is that it
    goes through the *same* machinery as a task and a project rather than a
    path of its own: `Paste.paste_create_transactions` builds it, the online
    pass resolves its `projects:` and `subscribers:`, and a `$local-id` in
    either waits for the item that declares it.
    """

    def test_a_paste_becomes_one_item_sent_to_paste_edit(self):
        phab = _phab()

        plan = _plan(
            phab,
            {"pastes": [{"title": "Release notes", "content": "hi", "language": "md"}]},
        )

        assert [item.object_type for item in plan.items] == ["paste"]
        assert {one["type"] for one in plan.items[0].transactions} == {
            "title",
            "text",
            "language",
        }
        assert EDIT_ENDPOINTS["paste"] == "paste"

    def test_the_transaction_is_title_and_never_name(self):
        """`paste.edit` names it `title`; the preview labels it "Name"."""
        phab = _phab()

        plan = _plan(phab, {"pastes": [{"title": "Notes"}]})
        sent = {one["type"]: one["value"] for one in plan.items[0].transactions}

        assert sent == {"title": "Notes"}
        assert plan.items[0].display["title"] == "Notes"
        assert plan.items[0].display["changes"][0]["field"] == "Name"

    def test_a_paste_with_no_title_is_reported_rather_than_dropped(self):
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(phab, {"pastes": [{"content": "orphan"}]})

        assert "pastes[0]" in str(excinfo.value)
        assert "title" in str(excinfo.value)

    def test_its_projects_and_subscribers_are_resolved(self):
        phab = _phab()

        plan = _plan(
            phab,
            {
                "pastes": [
                    {
                        "title": "Notes",
                        "projects": ["Backend Team"],
                        "subscribers": ["alice"],
                    }
                ]
            },
        )
        sent = {one["type"]: one["value"] for one in plan.items[0].transactions}

        assert sent["projects.add"] == ["PHID-PROJ-backend"]
        assert sent["subscribers.add"] == ["PHID-USER-alice"]
        # The preview names people and projects, never their PHIDs.
        shown = {one["field"]: one["new"] for one in plan.items[0].display["changes"]}
        assert shown["Tags"] == "Backend Team"
        assert shown["Subscribers"] == "alice"

    def test_a_local_id_makes_the_paste_wait_for_the_project(self):
        """A paste tagged into a project the same document creates."""
        phab = _phab()

        plan = _plan(
            phab,
            {
                "projects": [{"id": "home", "name": "New Home"}],
                "pastes": [{"title": "Notes", "projects": ["$home"]}],
            },
        )
        paste = next(item for item in plan.items if item.object_type == "paste")

        # The link is by *path*: `_linked` turns the `$home` the file wrote
        # into the item that declares it, because an item is its path.
        assert paste.depends_on == ("projects[0]",)
        # The transaction still holds the literal, substituted at apply time.
        sent = {one["type"]: one["value"] for one in paste.transactions}
        assert sent["projects.add"] == ["$home"]
        # The project is created first, whatever order the file wrote them in.
        assert [item.object_type for item in plan.items] == ["project", "paste"]

    def test_a_paste_never_sends_set_or_remove(self):
        phab = _phab()

        plan = _plan(
            phab,
            {"pastes": [{"title": "Notes", "projects": ["Backend Team"]}]},
        )

        assert not any(
            one["type"].endswith((".set", ".remove"))
            for one in plan.items[0].transactions
        )

    def test_nothing_builds_a_paste_app_for_a_spec_with_no_paste(self):
        """One sibling per object type the document names, and no others."""
        phab = _phab()

        with patch("phabfive.create.dispatch.app_for") as app_for:
            _plan(phab, {"tasks": [_task()]})

        app_for.assert_not_called()
