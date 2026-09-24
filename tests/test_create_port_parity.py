# -*- coding: utf-8 -*-

"""What the port of `create_tasks_from_config` had to keep (#480).

The four hundred lines that became `phabfive.spec.create` already got several
things right, and a port is exactly where those get lost quietly. Each one is
pinned here against the entry point the command still calls, rather than
against the plan, because the entry point is what a template's author sees.

`tests/test_spec_create.py` pins what the plan *is*; this pins that it still
creates what the recursion created. The two deliberate changes - a
description is no longer required, and a title-less childless item is an
error rather than a silent skip - are pinned there and are not undone here.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import PhabfiveDataException
from phabfive.maniphest.core import Maniphest

# Two milestones sharing a name, a project whose hashtag is another's name
PROJECTS = {
    "PHID-PROJ-backend": {"name": "Backend Team", "slugs": ["backend"]},
    "PHID-PROJ-hotfix": {"name": "Hotfixes", "slugs": ["deploy"]},
    "PHID-PROJ-deploy": {"name": "Deploy", "slugs": []},
    "PHID-PROJ-s1a": {"name": "Sprint 1", "slugs": []},
    "PHID-PROJ-s1b": {"name": "Sprint 1", "slugs": []},
}

USERS = {"alice": "PHID-USER-alice", "bob": "PHID-USER-bob"}


def _phab():
    phab = MagicMock()
    phab.project.query.return_value = {"data": PROJECTS}
    phab.project.search.return_value = {"data": []}
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
    phab.maniphest.search.return_value = {
        "data": [{"id": 7, "phid": "PHID-TASK-7"}, {"id": 8, "phid": "PHID-TASK-8"}]
    }

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


def _create(phab, tasks, **config):
    return _maniphest(phab).create_tasks_from_config({**config, "tasks": tasks})


def _task(title="Task", **fields):
    return {"title": title, "description": "x", **fields}


def _sent(phab):
    """Every call's transactions, in the order they were sent."""
    return [call.kwargs["transactions"] for call in phab.maniphest.edit.call_args_list]


def _by_title(phab):
    return {
        next(one["value"] for one in transactions if one["type"] == "title"): (
            transactions
        )
        for transactions in _sent(phab)
    }


class TestNothingIsCreatedUntilEverythingResolves:
    def test_a_bad_name_in_the_last_task_leaves_nothing_behind(self):
        tasks = [_task(f"Task {n}", projects=["Backend Team"]) for n in range(9)]
        tasks.append(_task("Task 9", tasks=[_task("Sub", assignment="nosuch")]))
        phab = _phab()

        with pytest.raises(PhabfiveDataException, match="nosuch"):
            _create(phab, tasks)

        phab.maniphest.edit.assert_not_called()

    def test_the_template_is_read_but_never_written(self):
        """A program hands the same dict in twice and gets the same result."""
        phab = _phab()
        config = {
            "variables": {"sprint": 42},
            "tasks": [_task("Sprint {{ sprint }}", priority="High")],
        }
        maniphest = _maniphest(phab)

        maniphest.create_tasks_from_config(config)
        maniphest.create_tasks_from_config(config)

        assert config["variables"] == {"sprint": 42}
        assert config["tasks"][0]["priority"] == "High"
        assert [
            one["value"]
            for transactions in _sent(phab)
            for one in transactions
            if one["type"] == "title"
        ] == ["Sprint 42", "Sprint 42"]


class TestEachNameIsResolvedOnce:
    def test_one_project_named_by_ten_tasks_costs_one_enumeration(self):
        phab = _phab()

        _create(phab, [_task(f"T{n}", projects=["Backend Team"]) for n in range(10)])

        assert phab.project.query.call_count == 1

    def test_one_user_named_by_ten_tasks_costs_one_search(self):
        phab = _phab()

        _create(
            phab,
            [
                _task(f"T{n}", assignment="alice", subscribers=["bob"])
                for n in range(10)
            ],
        )

        assert phab.user.search.call_count == 1

    def test_every_parent_monogram_in_the_document_is_one_search(self):
        """It used to be one `maniphest.search` per id, per task."""
        phab = _phab()

        _create(
            phab,
            [
                _task("One", parents=["T7"]),
                _task("Two", parents=["T8"], subtasks=["T7"]),
            ],
        )

        assert phab.maniphest.search.call_count == 1
        assert phab.maniphest.search.call_args.kwargs["constraints"] == {"ids": [7, 8]}


class TestNestingLinksWithAdd:
    def test_a_child_names_its_parent_and_the_parent_is_never_edited(self):
        phab = _phab()

        _create(phab, [_task("Parent", tasks=[_task("Child"), _task("Other")])])

        created = _by_title(phab)

        assert {"type": "parents.add", "value": ["PHID-TASK-1"]} in created["Child"]
        assert {"type": "parents.add", "value": ["PHID-TASK-1"]} in created["Other"]
        # The parent's own call carries no link back, and nothing was sent a
        # second time for it
        assert all(one["type"] != "parents.add" for one in created["Parent"])
        assert phab.maniphest.edit.call_count == 3

    def test_no_transaction_anywhere_would_discard_what_an_object_holds(self):
        phab = _phab()

        _create(
            phab,
            [
                _task(
                    "Parent",
                    projects=["Backend Team"],
                    subscribers=["alice"],
                    parents=["T7"],
                    subtasks=["T8"],
                    tasks=[_task("Child", projects=["Backend Team"])],
                )
            ],
        )

        types = [one["type"] for transactions in _sent(phab) for one in transactions]

        assert not [one for one in types if one.endswith((".set", ".remove"))]
        assert "projects.add" in types
        assert "subscribers.add" in types

    def test_children_are_created_after_their_parent_in_document_order(self):
        phab = _phab()

        result = _create(
            phab,
            [
                _task("First", tasks=[_task("First child")]),
                _task("Second"),
            ],
        )

        assert [
            one["value"]
            for transactions in _sent(phab)
            for one in transactions
            if one["type"] == "title"
        ] == ["First", "First child", "Second"]
        assert result == {"task_ids": [1, 2, 3]}


class TestProjectNames:
    @pytest.mark.parametrize(
        "written", ["Backend Team", "backend team", "BACKEND", "backend"]
    )
    def test_a_name_and_a_hashtag_both_match_without_case(self, written):
        phab = _phab()

        _create(phab, [_task(projects=[written])])

        assert {"type": "projects.add", "value": ["PHID-PROJ-backend"]} in (
            _sent(phab)[0]
        )

    def test_a_hashtag_wins_over_another_project_s_name(self):
        """'deploy' is the hashtag of Hotfixes and the name of another project."""
        phab = _phab()

        _create(phab, [_task(projects=["deploy"])])

        assert {"type": "projects.add", "value": ["PHID-PROJ-hotfix"]} in (
            _sent(phab)[0]
        )

    def test_an_ambiguous_name_is_refused_naming_the_candidates(self):
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _create(phab, [_task(projects=["Sprint 1"])])

        message = str(excinfo.value)

        assert "ambiguous" in message
        assert "PHID-PROJ-s1a" in message and "PHID-PROJ-s1b" in message
        phab.maniphest.edit.assert_not_called()


class TestTheReturnValue:
    def test_a_real_run_answers_with_the_ids_it_created(self):
        phab = _phab()

        result = _create(phab, [_task("One"), _task("Two")])

        assert result == {"task_ids": [1, 2]}

    def test_a_dry_run_answers_with_the_preview_the_command_prints(self):
        phab = _phab()

        result = _maniphest(phab).create_tasks_from_config(
            {
                "tasks": [
                    _task(
                        "Parent",
                        assignment="PHID-USER-alice",
                        subscribers=["@bob", "@me"],
                        tasks=[_task("Child")],
                    )
                ]
            },
            dry_run=True,
        )

        assert result == {
            "dry_run": True,
            "tasks": [
                {
                    "depth": 1,
                    "title": "Parent",
                    "assignee": "alice",
                    "subscribers": ["bob", "caller"],
                    "commits": [],
                },
                {
                    "depth": 2,
                    "title": "Child",
                    "assignee": None,
                    "subscribers": [],
                    "commits": [],
                },
            ],
        }
        phab.maniphest.edit.assert_not_called()

    def test_a_server_refusal_reaches_the_caller_with_the_records(self):
        """What the server refused, and what exists now, in one exception.

        The refusal itself is unchanged - same sentence, same `__cause__` -
        and what is added is the record per object, which is what a template
        whose fiftieth task is refused needs and what raising the server's
        exception straight through used to throw away (#485).
        """
        from phabfive.spec.create import CreateFailed

        phab = _phab()
        phab.maniphest.edit.side_effect = RuntimeError("ERR-CONDUIT-CORE: nope")

        with pytest.raises(CreateFailed) as excinfo:
            _create(phab, [_task()])

        assert "ERR-CONDUIT-CORE" in excinfo.value.report.failures[0].reason
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_what_was_created_before_the_refusal_is_in_the_report(self):
        """The whole point: three tasks, the second refused, all three named."""
        from phabfive.spec.create import CreateFailed

        phab = _phab()
        phab.maniphest.edit.side_effect = [
            {"object": {"id": 1, "phid": "PHID-TASK-1"}},
            RuntimeError("ERR-CONDUIT-CORE: nope"),
        ]

        with pytest.raises(CreateFailed) as excinfo:
            _create(
                phab,
                [_task(title="One"), _task(title="Two"), _task(title="Three")],
            )

        report = excinfo.value.report

        assert [(one.title, one.status) for one in report.records] == [
            ("One", "created"),
            ("Two", "failed"),
            ("Three", "skipped"),
        ]
        assert report.task_ids == [1]


class TestTheShippedTemplates:
    """Every shipped create spec still plans, large-programme included."""

    @pytest.mark.parametrize(
        "path, tasks",
        [
            ("specs/create/sprint-tasks.yaml", 3),
            ("specs/create/feature-epic.yaml", 9),
            ("specs/create/large-programme.yaml", 66),
        ],
    )
    def test_every_task_in_it_reaches_the_plan(self, path, tasks):
        """What is under test is the shape, not the fixture.

        The names these specs use are not on any instance this suite
        has, so resolving them would report sixty problems and say nothing
        about whether the document was walked. An **empty** `Resolution()`
        is how a caller asks for that on purpose: `plan_create` uses a
        resolution as given and never adds to it, so every reference-valued
        key comes back unanswered and is left out. That is the shape of the
        document and not a plan to apply, which is why `validate=False`
        alone no longer means it - it would silently plan bare objects.
        """
        from phabfive.spec import load_spec
        from phabfive.spec.create import plan_create
        from phabfive.spec.online import Resolution

        spec = load_spec(path, kind="create").render()

        plan = plan_create(
            _maniphest(_phab()), spec, validate=False, resolution=Resolution()
        )

        assert len(plan.items) == tasks
        assert all(item.display.get("title") for item in plan.creating)
