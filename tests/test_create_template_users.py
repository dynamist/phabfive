# -*- coding: utf-8 -*-

"""Users in a creation template (#461).

`maniphest create --with` takes `assignment` and `subscribers` the way every
option that takes a user does - username, @username, @me or a user PHID, in
any case - and asks the instance only about the users the template names, all
of them before any task is created.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest.core import Maniphest

runner = CliRunner()

USERS = {"alice": "PHID-USER-alice", "bob": "PHID-USER-bob"}
CALLER = {"phid": "PHID-USER-caller", "userName": "caller"}


def _phab(users_called_me=()):
    """A client that knows alice and bob, and whoever `users_called_me` adds."""
    phab = MagicMock()
    phab.user.whoami.return_value = dict(CALLER)
    records = [
        {"phid": phid, "fields": {"username": name}} for name, phid in USERS.items()
    ] + [{"phid": phid, "fields": {"username": "me"}} for phid in users_called_me]

    def search(constraints):
        names = {n.casefold() for n in constraints.get("usernames", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                r
                for r in records
                if r["fields"]["username"].casefold() in names or r["phid"] in phids
            ]
        }

    phab.user.search.side_effect = search
    phab.project.search.return_value = {"data": []}

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


def _create(phab, tasks, variables=None, dry_run=False):
    return _maniphest(phab).create_tasks_from_config(
        {"variables": variables or {}, "tasks": tasks}, dry_run=dry_run
    )


def _task(title="Task", **fields):
    return {"title": title, "description": "x", **fields}


def _transactions(phab, call=0):
    return phab.maniphest.edit.call_args_list[call].kwargs["transactions"]


class TestAssignment:
    def test_it_sets_the_owner(self):
        """It used to be dropped without a word."""
        phab = _phab()

        _create(phab, [_task(assignment="alice")])

        assert {"type": "owner", "value": "PHID-USER-alice"} in _transactions(phab)

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("@alice", "PHID-USER-alice"),
            ("ALICE", "PHID-USER-alice"),
            ("PHID-USER-bob", "PHID-USER-bob"),
            ("@me", "PHID-USER-caller"),
        ],
    )
    def test_every_spelling_works(self, value, expected):
        phab = _phab()

        _create(phab, [_task(assignment=value)])

        assert {"type": "owner", "value": expected} in _transactions(phab)

    def test_it_renders_variables(self):
        phab = _phab()

        _create(phab, [_task(assignment="{{ lead }}")], variables={"lead": "bob"})

        assert {"type": "owner", "value": "PHID-USER-bob"} in _transactions(phab)

    def test_without_one_there_is_no_owner(self):
        phab = _phab()

        _create(phab, [_task()])

        assert all(t["type"] != "owner" for t in _transactions(phab))

    def test_a_list_is_refused(self):
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="assignment takes one"):
            _create(phab, [_task(assignment=["alice", "bob"])])

        phab.maniphest.edit.assert_not_called()


class TestSubscribers:
    def test_every_spelling_works(self):
        phab = _phab()

        _create(phab, [_task(subscribers=["@alice", "PHID-USER-bob", "@me"])])

        assert {
            "type": "subscribers.add",
            "value": ["PHID-USER-alice", "PHID-USER-bob", "PHID-USER-caller"],
        } in _transactions(phab)

    def test_case_does_not_matter(self):
        """The old lookup was a case-exact dict key."""
        phab = _phab()

        _create(phab, [_task(subscribers=["ALICE"])])

        assert {"type": "subscribers.add", "value": ["PHID-USER-alice"]} in (
            _transactions(phab)
        )

    def test_one_user_spelled_twice_is_subscribed_once(self):
        phab = _phab()

        _create(phab, [_task(subscribers=["alice", "@Alice", "PHID-USER-alice"])])

        assert {"type": "subscribers.add", "value": ["PHID-USER-alice"]} in (
            _transactions(phab)
        )

    def test_they_render_variables(self):
        phab = _phab()

        _create(phab, [_task(subscribers=["{{ dev }}"])], variables={"dev": "bob"})

        assert {"type": "subscribers.add", "value": ["PHID-USER-bob"]} in (
            _transactions(phab)
        )

    @pytest.mark.parametrize(
        "subscribers", ["alice", {"alice": "bob"}], ids=["string", "mapping"]
    )
    def test_anything_but_a_list_is_refused(self, subscribers):
        """Iterating a string asks for each letter as a user, a mapping for its keys."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="subscribers takes a list"):
            _create(phab, [_task(subscribers=subscribers)])

        phab.maniphest.edit.assert_not_called()

    @pytest.mark.parametrize("item", [None, 1234], ids=["null", "number"])
    def test_an_item_that_is_not_a_username_is_refused(self, item):
        """A stray `-` in YAML is a null item, and a bare 1234 is a number."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="subscribers takes"):
            _create(phab, [_task(subscribers=["alice", item])])

        phab.maniphest.edit.assert_not_called()


class TestMeIsAKeyword:
    """A user called "me" does not take `@me` from everybody else (#496)."""

    def test_assignment_resolves_to_the_caller(self):
        phab = _phab(users_called_me=["PHID-USER-other"])

        _create(phab, [_task(assignment="@me")])

        assert {"type": "owner", "value": "PHID-USER-caller"} in _transactions(phab)

    def test_in_subscribers_too(self):
        phab = _phab(users_called_me=["PHID-USER-other"])

        _create(phab, [_task(subscribers=["@me"])])

        assert {
            "type": "subscribers.add",
            "value": ["PHID-USER-caller"],
        } in _transactions(phab)


class TestResolvedUpFront:
    def test_an_unknown_user_in_the_last_task_creates_nothing(self):
        """A typo in the tenth task used to leave nine created."""
        phab = _phab()
        tasks = [_task(f"Task {n}", assignment="alice") for n in range(9)]
        tasks.append(_task("Task 9", tasks=[_task("Sub", subscribers=["nosuch"])]))

        with pytest.raises(PhabfiveDataException, match="No such user: 'nosuch'"):
            _create(phab, tasks)

        phab.maniphest.edit.assert_not_called()

    def test_every_unknown_user_is_named(self):
        """And each one is named where it is, which is new since #480."""
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _create(
                phab,
                [
                    _task(subscribers=["typo1"]),
                    _task(tasks=[_task(subscribers=["alice", "typo2"])]),
                ],
            )

        assert str(excinfo.value) == (
            "2 problem(s) in this spec:\n"
            "  - tasks[0].subscribers[0]: No such user: 'typo1'\n"
            "  - tasks[1].tasks[0].subscribers[1]: No such user: 'typo2'"
        )
        phab.maniphest.edit.assert_not_called()

    def test_only_the_named_users_are_asked_about(self):
        """No unpaged user.search for everybody, which saw only the first 100.

        One request for the whole document since #480, where there used to
        be one per option: `assignment:` and `subscribers:` are the same
        question asked of the same endpoint, and the layer-2 resolver asks
        it once with every distinct name in the spec.
        """
        phab = _phab()

        _create(
            phab,
            [
                _task(assignment="alice", subscribers=["bob"]),
                _task(assignment="alice", tasks=[_task(subscribers=["bob"])]),
            ],
        )

        calls = [c.kwargs.get("constraints") for c in phab.user.search.call_args_list]
        assert calls == [{"usernames": ["alice", "bob"]}]

    def test_a_template_naming_nobody_asks_about_nobody(self):
        phab = _phab()

        _create(phab, [_task()])

        phab.user.search.assert_not_called()


class TestDryRun:
    def test_it_previews_the_assignee_and_subscribers(self):
        phab = _phab()

        result = _create(
            phab,
            [
                _task(
                    "Parent",
                    assignment="PHID-USER-alice",
                    subscribers=["@bob", "@me"],
                    tasks=[_task("Child")],
                )
            ],
            dry_run=True,
        )

        assert result["tasks"] == [
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
        ]
        phab.maniphest.edit.assert_not_called()

    def test_the_cli_answers_with_the_plan(self, tmp_path):
        """What `--with --dry-run` shows, now that it is `apply -f`.

        The human preview is one line per object - the tree
        `phabfive.cli.spec_run._item_line` prints - and it names the object
        type, which the old flat list did not: a spec creating a project
        and two tasks used to preview as three indistinguishable bullets.

        **What it no longer prints are the `Assignee:` and `Subscribers:`
        sub-lines.** That is a deliberate trade for one preview across the
        three `create --with` commands and `apply -f`, and the information
        is not lost: it is on the item's `display` in every machine format,
        which is what a dry run is for reading with. Asserted here, both
        ways round, so neither half can go quietly.
        """
        import phabfive.create
        from phabfive.spec.create import CreateItem, CreatePlan

        maniphest = MagicMock()
        plan = CreatePlan(
            items=(
                CreateItem(
                    object_type="task",
                    path="tasks[0]",
                    display={
                        "title": "Parent",
                        "assignee": "alice",
                        "subscribers": ["bob", "carol"],
                    },
                ),
                CreateItem(
                    object_type="task",
                    path="tasks[0].tasks[0]",
                    depth=1,
                    parent_path="tasks[0]",
                    display={"title": "Child", "assignee": None, "subscribers": []},
                ),
            )
        )
        template = tmp_path / "tasks.yaml"
        template.write_text(
            "spec: phorge/v1alpha1\nkind: create\ntasks:\n  - title: Parent\n"
        )

        def run(output_format):
            with patch(
                "phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest
            ):
                with patch.object(phabfive.create, "plan_spec", return_value=plan):
                    return runner.invoke(
                        app,
                        [
                            f"--format={output_format}",
                            "maniphest",
                            "create",
                            "--with",
                            str(template),
                            "--dry-run",
                        ],
                    )

        human = run("rich")

        assert human.exit_code == 0, human.output
        assert human.stdout.splitlines() == [
            f"[DRY RUN] {template}: would create 2 tasks.",
            "  - task 'Parent'",
            "    - task 'Child'",
        ]

        machine = run("json")

        assert machine.exit_code == 0, machine.output

        records = json.loads(machine.stdout)

        assert records[0]["display"]["assignee"] == "alice"
        assert records[0]["display"]["subscribers"] == ["bob", "carol"]
