# -*- coding: utf-8 -*-

"""Planning and applying an edit spec (`phabfive.edit.spec`).

The planner builds no transaction itself: it hands each key of an item to
`Maniphest.build_task_edit`, the engine `phabfive edit` uses. So what is
pinned here is the translation - a stated *result* into that engine's *add
and remove* - and the rules around it: nothing is planned for a key that is
absent, a refusal is reported under the key that caused it, the stamp guards
a change and not a look, and a plan holding a problem applies nothing.

The engine is stood in for by `FakeManiphest`, which records what it was
asked and answers one transaction per argument. The real engine against a
real Phorge is `tests/e2e`.
"""

import pytest

from phabfive.edit.spec import (
    EditPlanError,
    EditReport,
    apply_edit_plan,
    apply_edit_spec,
    plan_edit,
)
from phabfive.exceptions import (
    PhabfiveConnectionException,
    PhabfiveDataException,
    PhabfiveInputException,
    PhabfiveNotFoundException,
)
from phabfive.spec import Spec

MODIFIED = 1769851800  # 2026-01-31T09:30:00Z
STAMP = "2026-01-31T09:30:00Z"


def task(task_id, **overrides):
    data = {
        "id": int(task_id),
        "phid": f"PHID-TASK-{task_id}",
        "fields": {"name": f"Task {task_id}", "dateModified": MODIFIED},
        "attachments": {
            "projects": {"projectPHIDs": ["PHID-PROJ-a", "PHID-PROJ-b"]},
            "subscribers": {"subscriberPHIDs": ["PHID-USER-alice"]},
            "columns": {"boards": {"PHID-PROJ-a": {"columns": []}}},
        },
    }
    data.update(overrides)
    return data


class FakeManiphest:
    """The parts of `Maniphest` the planner reaches, answering from tables."""

    def __init__(self, tasks=None, edges=None):
        self.tasks = tasks if tasks is not None else {"1": task("1")}
        self.edges = edges or {}
        self.asked = []
        self.applied = []
        self.unknown = set()
        self.refuse_apply = set()
        self.conf = {}
        self.phab = self

    # --- what the planner calls -------------------------------------------

    def _get_task_data(self, task_id):
        if task_id not in self.tasks:
            raise PhabfiveNotFoundException(f"Task T{task_id} not found")
        return self.tasks[task_id]

    def _named(self, prefix, values):
        missing = [value for value in values if value in self.unknown]
        if missing:
            raise PhabfiveNotFoundException(f"No such: {', '.join(missing)}")
        return {
            value: (
                value if value.startswith("PHID-") else f"PHID-{prefix}-{value}",
                value,
            )
            for value in values
        }

    def _resolve_project_tags(self, values, option=None):
        return self._named("PROJ", [value.lstrip("#") for value in values])

    def _resolve_users(self, values, option=None):
        return self._named("USER", values)

    def _task_refs(self, values, option=None):
        return self._named("TASK", [value.lstrip("T") for value in values])

    def build_task_edit(self, task_id, task_data, **arguments):
        self.asked.append(arguments)

        for name, value in arguments.items():
            if isinstance(value, str) and value in self.unknown:
                raise PhabfiveDataException(f"{value!r} is not known here")

        current = task_data["fields"]
        transactions = []

        for name, value in arguments.items():
            if name == "board_phids" or not value:
                continue
            if current.get(name) == value:
                continue
            transactions.append({"type": name, "value": value})

        changes = [
            {"field": one["type"], "old": None, "new": str(one["value"])}
            for one in transactions
        ]

        return transactions, changes

    def apply_task_edit(self, task_id, transactions):
        if task_id in self.refuse_apply:
            raise PhabfiveDataException(f"T{task_id} refused")
        self.applied.append((task_id, transactions))


@pytest.fixture(autouse=True)
def edges(monkeypatch):
    """`fetch_task_edges` answers from the fake's table."""

    def fetch(phab, task_phid, kind):
        return list(phab.edges.get((task_phid, kind), []))

    monkeypatch.setattr("phabfive.maniphest.fetchers.fetch_task_edges", fetch)


def edit(*items, **envelope):
    return Spec.from_data({"kind": "edit", **envelope, "tasks": list(items)})


def planned(maniphest, *items, **options):
    return plan_edit(maniphest, edit(*items), **options)


def sent(maniphest):
    """Every argument the engine was handed, merged, `board_phids` aside."""
    merged = {}
    for arguments in maniphest.asked:
        merged.update(arguments)
    return merged


class TestWhatIsNotWrittenIsNotAsked:
    def test_an_item_naming_only_its_task_plans_nothing(self):
        maniphest = FakeManiphest()
        plan = planned(maniphest, {"task": "T1"})

        assert maniphest.asked == []
        assert [item.noop for item in plan.items] == [True]

    def test_a_key_with_nothing_after_it_is_not_asked_either(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "status": None, "subscribers": None})

        assert maniphest.asked == []

    def test_only_the_keys_written_reach_the_engine(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "status": "resolved"})

        assert maniphest.asked == [{"status": "resolved"}]


class TestScalars:
    def test_each_key_is_the_engines_argument(self):
        maniphest = FakeManiphest()
        planned(
            maniphest,
            {
                "task": "T1",
                "title": "New",
                "priority": "high",
                "status": "open",
                "assignment": "bob",
                "description": "Text",
                "space": "S2",
                "visible-to": "users",
                "editable-by": "#infra",
            },
        )

        assert sent(maniphest) == {
            "title": "New",
            "priority": "high",
            "status": "open",
            "assign": "bob",
            "description": "Text",
            "space": "S2",
            "visible_to": "users",
            "editable_by": "#infra",
        }

    def test_nobody_is_passed_through_for_the_engine_to_clear(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "assignment": "@none"})

        assert sent(maniphest) == {"assign": "@none"}

    def test_the_monogram_is_read_whatever_its_spacing(self):
        plan = planned(FakeManiphest(), {"task": " T1 ", "status": "open"})

        assert plan.items[0].monogram == "T1"


class TestAFullSetBecomesAddAndRemove:
    def test_projects_not_in_the_set_are_removed(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "projects": ["a", "c"]})

        arguments = sent(maniphest)

        assert list(arguments["tag"]) == ["a", "c"]
        assert arguments["untag"] == ["PHID-PROJ-b"]

    def test_an_empty_list_removes_everything(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "projects": []})

        assert sent(maniphest) == {"tag": {}, "untag": ["PHID-PROJ-a", "PHID-PROJ-b"]}

    def test_a_set_that_matches_removes_nothing(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "projects": ["#a", "b"]})

        assert sent(maniphest)["untag"] == []

    def test_subscribers(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "subscribers": ["bob"]})

        assert sent(maniphest) == {
            "subscribe": ["bob"],
            "unsubscribe": ["PHID-USER-alice"],
        }

    def test_parents_and_subtasks_go_to_the_engine_together(self):
        maniphest = FakeManiphest(
            edges={
                ("PHID-TASK-1", "parents"): ["PHID-TASK-9"],
                ("PHID-TASK-1", "subtasks"): ["PHID-TASK-5"],
            }
        )
        planned(maniphest, {"task": "T1", "parents": ["T2"], "subtasks": ["T5"]})

        assert len(maniphest.asked) == 1
        arguments = maniphest.asked[0]

        assert list(arguments["parent"]) == ["2"]
        assert arguments["unparent"] == ["PHID-TASK-9"]
        assert list(arguments["subtask"]) == ["5"]
        assert "unsubtask" not in arguments

    def test_commits(self, monkeypatch):
        monkeypatch.setattr(
            "phabfive.commits.resolve_commit_phids",
            lambda phab, values, option=None: {
                value: (f"PHID-CMIT-{value}", value) for value in values
            },
        )
        maniphest = FakeManiphest(
            edges={("PHID-TASK-1", "commits"): ["PHID-CMIT-old", "PHID-CMIT-rXabc"]}
        )
        planned(maniphest, {"task": "T1", "commits": ["rXabc"]})

        assert sent(maniphest) == {"attach": ["rXabc"], "detach": ["PHID-CMIT-old"]}


class TestASubscriberThatIsNotAUser:
    """A project or a package can subscribe; an export writes it as its PHID."""

    def with_package(self):
        watched = task("1")
        watched["attachments"]["subscribers"]["subscriberPHIDs"] = [
            "PHID-USER-alice",
            "PHID-OPKG-1",
        ]
        return FakeManiphest(tasks={"1": watched})

    def test_a_full_set_naming_it_keeps_it_and_asks_nothing(self):
        maniphest = self.with_package()
        plan = planned(
            maniphest, {"task": "T1", "subscribers": ["PHID-USER-alice", "PHID-OPKG-1"]}
        )

        assert plan.ok
        assert maniphest.asked == []

    def test_a_full_set_leaving_it_out_is_refused_not_guessed(self):
        plan = planned(self.with_package(), {"task": "T1", "subscribers": ["alice"]})

        assert [(p.field, p.code) for p in plan.problems] == [
            ("subscribers", "unknown-user")
        ]
        assert "not a user" in plan.problems[0].reason

    def test_adding_one_is_refused_too(self):
        plan = planned(FakeManiphest(), {"task": "T1", "subscribers": ["+PHID-PROJ-x"]})

        assert [p.code for p in plan.problems] == ["unknown-user"]

    def test_someone_already_subscribed_is_not_added_again(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "subscribers": ["+PHID-USER-alice"]})

        assert maniphest.asked == []


class TestADeltaLeavesTheRest:
    def test_projects(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "projects": ["+c", "-b"]})

        arguments = sent(maniphest)

        assert list(arguments["tag"]) == ["c"]
        assert list(arguments["untag"]) == ["b"]

    def test_subscribers(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "subscribers": ["+bob"]})

        assert sent(maniphest) == {"subscribe": ["bob"], "unsubscribe": []}

    def test_the_current_list_is_not_even_read(self):
        """A delta needs no edge lookup of its own; the engine does its own."""
        maniphest = FakeManiphest(edges=None)
        maniphest.edges = None  # reading it would raise

        planned(maniphest, {"task": "T1", "parents": ["+T2"]})

        assert list(maniphest.asked[0]["parent"]) == ["2"]


class TestTheColumnNeedsOneBoard:
    def test_the_board_the_task_is_on(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "column": "Done"})

        assert maniphest.asked == [
            {"tag": {}, "untag": {}, "column": "Done", "board_phids": ["PHID-PROJ-a"]}
        ]

    def test_a_task_on_none_uses_the_one_project_the_item_tags(self):
        bare = task("1")
        bare["attachments"]["columns"]["boards"] = {}
        maniphest = FakeManiphest(tasks={"1": bare})

        planned(maniphest, {"task": "T1", "projects": ["+c"], "column": "Done"})

        assert maniphest.asked[0]["board_phids"] == ["PHID-PROJ-c"]

    def test_a_task_on_none_and_tagged_into_none_is_refused(self):
        bare = task("1")
        bare["attachments"]["columns"]["boards"] = {}
        plan = planned(FakeManiphest(tasks={"1": bare}), {"task": "T1", "column": "X"})

        assert [(p.field, p.code) for p in plan.problems] == [
            ("column", "unknown-column")
        ]

    def test_a_task_on_several_is_refused_rather_than_guessed(self):
        both = task("1")
        both["attachments"]["columns"]["boards"]["PHID-PROJ-b"] = {"columns": []}
        plan = planned(FakeManiphest(tasks={"1": both}), {"task": "T1", "column": "X"})

        assert [p.code for p in plan.problems] == ["unknown-column"]
        assert "2 workboards" in plan.problems[0].reason

    def test_a_board_the_item_takes_the_task_off_does_not_count(self):
        both = task("1")
        both["attachments"]["columns"]["boards"]["PHID-PROJ-b"] = {"columns": []}
        maniphest = FakeManiphest(tasks={"1": both})

        planned(maniphest, {"task": "T1", "projects": ["-b"], "column": "Done"})

        assert maniphest.asked[0]["board_phids"] == ["PHID-PROJ-a"]


class TestEveryProblemIsReportedUnderItsKey:
    def test_a_task_that_does_not_exist(self):
        plan = planned(FakeManiphest(), {"task": "T404", "status": "open"})

        assert [(p.object, p.field, p.code, p.layer) for p in plan.problems] == [
            ("tasks[0]", "task", "unknown-reference", "online")
        ]
        assert plan.items == ()

    def test_two_bad_values_in_one_item_are_two_problems(self):
        maniphest = FakeManiphest()
        maniphest.unknown = {"nonsense", "ghost"}

        plan = planned(
            maniphest,
            {
                "task": "T1",
                "status": "nonsense",
                "subscribers": ["ghost"],
                "title": "ok",
            },
        )

        assert [(p.field, p.code) for p in plan.problems] == [
            ("status", "unknown-status"),
            ("subscribers", "unknown-user"),
        ]
        assert not plan.ok

    @pytest.mark.parametrize(
        "key,value,code",
        [
            ("priority", "nonsense", "unknown-priority"),
            ("assignment", "nonsense", "unknown-user"),
            ("space", "nonsense", "unknown-space"),
            ("projects", ["nonsense"], "unknown-project"),
            ("parents", ["T404"], "unknown-reference"),
        ],
    )
    def test_each_key_has_its_code(self, key, value, code):
        maniphest = FakeManiphest()
        maniphest.unknown = {"nonsense", "404"}

        plan = planned(maniphest, {"task": "T1", key: value})

        assert [(p.field, p.code) for p in plan.problems] == [(key, code)]

    def test_a_good_item_beside_a_bad_one_is_still_planned(self):
        maniphest = FakeManiphest(tasks={"1": task("1"), "2": task("2")})
        maniphest.unknown = {"nonsense"}

        plan = planned(
            maniphest,
            {"task": "T1", "status": "nonsense"},
            {"task": "T2", "status": "open"},
        )

        assert [item.monogram for item in plan.items] == ["T2"]
        assert [p.object for p in plan.problems] == ["tasks[0]"]

    def test_an_instance_that_cannot_be_asked_is_not_a_problem_with_the_file(self):
        maniphest = FakeManiphest()

        def unreachable(*args, **kwargs):
            raise PhabfiveConnectionException("no route")

        maniphest.build_task_edit = unreachable

        with pytest.raises(PhabfiveConnectionException):
            planned(maniphest, {"task": "T1", "status": "open"})


class TestTheStampGuardsAChange:
    def later(self):
        changed = task("1")
        changed["fields"]["dateModified"] = MODIFIED + 60
        return FakeManiphest(tasks={"1": changed})

    def test_a_change_to_a_task_modified_since_is_stale(self):
        plan = planned(
            self.later(), {"task": "T1", "modified": STAMP, "status": "open"}
        )

        assert [(p.field, p.code) for p in plan.problems] == [("modified", "stale")]
        assert "2026-01-31T09:31:00Z" in plan.problems[0].reason
        assert plan.items == ()

    def test_no_change_is_not_asked_about_the_stamp(self):
        """What keeps the second apply of a document from being refused."""
        plan = planned(self.later(), {"task": "T1", "modified": STAMP})

        assert plan.problems == ()
        assert [item.noop for item in plan.items] == [True]

    def test_force_plans_it_anyway(self):
        plan = planned(
            self.later(),
            {"task": "T1", "modified": STAMP, "status": "open"},
            force=True,
        )

        assert plan.ok
        assert len(plan.edits) == 1

    def test_a_task_not_modified_since_is_changed(self):
        plan = planned(
            FakeManiphest(), {"task": "T1", "modified": STAMP, "status": "open"}
        )

        assert plan.ok and len(plan.edits) == 1

    def test_an_item_without_a_stamp_is_not_checked(self):
        plan = planned(self.later(), {"task": "T1", "status": "open"})

        assert plan.ok and len(plan.edits) == 1


class TestThePlan:
    def test_it_refuses_a_spec_of_another_kind(self):
        create = Spec.from_data({"kind": "create", "tasks": [{"title": "t"}]})

        with pytest.raises(EditPlanError, match="create spec"):
            plan_edit(FakeManiphest(), create)

    def test_it_refuses_one_the_offline_layer_has_errors_for(self):
        maniphest = FakeManiphest()

        with pytest.raises(EditPlanError) as raised:
            planned(maniphest, {"task": "T1", "priority": "raise"})

        assert [p.code for p in raised.value.problems] == ["relative-value"]
        assert maniphest.asked == []

    def test_its_records_say_what_would_happen(self):
        maniphest = FakeManiphest(tasks={"1": task("1"), "2": task("2")})
        plan = planned(maniphest, {"task": "T1", "status": "open"}, {"task": "T2"})

        assert plan.as_records() == [
            {
                "type": "task",
                "path": "tasks[0]",
                "monogram": "T1",
                "title": "Task 1",
                "status": "would-edit",
                "changes": [{"field": "status", "old": None, "new": "open"}],
            },
            {
                "type": "task",
                "path": "tasks[1]",
                "monogram": "T2",
                "title": "Task 2",
                "status": "unchanged",
                "changes": [],
            },
        ]

    def test_planning_writes_nothing(self):
        maniphest = FakeManiphest()
        planned(maniphest, {"task": "T1", "status": "open"})

        assert maniphest.applied == []


class TestApplying:
    def three(self):
        return FakeManiphest(tasks={n: task(n) for n in ("1", "2", "3")})

    def test_only_what_changes_is_sent(self):
        maniphest = self.three()
        plan = planned(maniphest, {"task": "T1", "status": "open"}, {"task": "T2"})

        report = apply_edit_spec(maniphest, plan)

        assert [record.status for record in report.records] == ["edited", "unchanged"]
        assert maniphest.applied == [("1", [{"type": "status", "value": "open"}])]
        assert report.ok
        assert report.summary == "1 of 2 objects edited, 1 already as written."

    def test_a_refusal_stops_the_run_and_says_what_landed(self):
        maniphest = self.three()
        maniphest.refuse_apply = {"2"}
        plan = planned(
            maniphest,
            {"task": "T1", "status": "open"},
            {"task": "T2", "status": "open"},
            {"task": "T3", "status": "open"},
        )

        report = apply_edit_spec(maniphest, plan)

        assert [record.status for record in report.records] == [
            "edited",
            "failed",
            "skipped",
        ]
        assert [task_id for task_id, _ in maniphest.applied] == ["1"]
        assert not report.ok
        assert "T2" in report.summary and "1 not attempted" in report.summary

    def test_a_plan_holding_a_problem_is_refused_whole(self):
        maniphest = self.three()
        maniphest.unknown = {"nonsense"}
        plan = planned(
            maniphest,
            {"task": "T1", "status": "open"},
            {"task": "T2", "status": "nonsense"},
        )

        with pytest.raises(EditPlanError, match="not applied"):
            list(apply_edit_plan(maniphest, plan))

        assert maniphest.applied == []

    def test_an_empty_report_is_ok(self):
        assert EditReport().ok
        assert EditReport().summary == "0 of 0 objects edited."

    def test_a_record_is_a_plain_mapping(self):
        maniphest = self.three()
        plan = planned(maniphest, {"task": "T1", "status": "open"})
        [record] = apply_edit_plan(maniphest, plan)

        assert record.as_record() == {
            "type": "task",
            "path": "tasks[0]",
            "status": "edited",
            "monogram": "T1",
            "title": "Task 1",
            "changes": [{"field": "status", "old": None, "new": "open"}],
            "reason": None,
        }


def test_validate_online_is_the_plan(monkeypatch):
    """`spec validate` on an edit document reports what applying would."""
    maniphest = FakeManiphest()
    maniphest.unknown = {"nonsense"}
    spec = edit({"task": "T1", "status": "nonsense"})

    assert [problem.code for problem in spec.validate_online(maniphest)] == [
        "unknown-status"
    ]
    assert maniphest.applied == []


def test_a_mixed_list_never_reaches_the_planner():
    with pytest.raises(EditPlanError) as raised:
        planned(FakeManiphest(), {"task": "T1", "projects": ["a", "+b"]})

    assert isinstance(raised.value, PhabfiveInputException)
