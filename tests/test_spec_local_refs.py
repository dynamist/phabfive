# -*- coding: utf-8 -*-

"""`$local-id`: naming what the same spec creates, and creating it first (#483).

The point of a unified create spec is one file that creates a project and
the tasks tagged into it. A task tagged into a project the same document
creates needs a PHID that does not exist until apply time, so the grammar
has a sixth spelling - ``$platform`` - which names an object *this spec*
makes rather than one the instance has.

Three properties are pinned here, and they are the whole feature:

1. **The questions a file can answer are answered offline.** An unknown
   ``$id``, a duplicate ``id:``, a reference cycle and a ``$ref`` to a
   grouping that creates nothing are all questions about the document, so
   they are reported with no token, no configuration and no socket - proved
   out of process, because by the time the rest of the suite runs everything
   is already imported.
2. **The planner orders what it creates.** The object a ``$ref`` names is
   created before the item that names it, whatever order they were written
   in, and ``$local`` and ``T123`` mix freely in one list.
3. **The substitution happens as it goes.** A planned transaction holds the
   literal string ``"$platform"``; `apply_plan` replaces it with the PHID
   the project was actually created as, one object at a time.

`tests/test_spec_create.py` owns what a plan *is* and
`tests/test_spec_existing_refs.py` what an anchor to an existing object is.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import PhabfiveDataException
from phabfive.maniphest.core import Maniphest
from phabfive.spec import Spec
from phabfive.spec.create import apply_plan, plan_create

REPOSITORY = Path(__file__).resolve().parent.parent

PROJECTS = {
    "PHID-PROJ-backend": {"name": "Backend Team", "slugs": ["backend"]},
}

USERS = {"alice": "PHID-USER-alice"}

TASKS = {7: "PHID-TASK-7"}


def _phab():
    """A client that knows one project, one user, one task, and writes both."""
    phab = MagicMock()
    phab.project.query.return_value = {"data": PROJECTS}
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller", "userName": "caller"}

    def user_search(constraints):
        names = {name.casefold() for name in constraints.get("usernames", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                {"phid": phid, "fields": {"username": name}}
                for name, phid in USERS.items()
                if name in names or phid in phids
            ]
        }

    phab.user.search.side_effect = user_search

    def task_search(constraints, **kwargs):
        wanted = set(constraints.get("ids", []))
        found = set(constraints.get("phids", []))
        return {
            "data": [
                {"id": task_id, "phid": phid}
                for task_id, phid in TASKS.items()
                if task_id in wanted or phid in found
            ],
            "cursor": {"after": None},
        }

    phab.maniphest.search.side_effect = task_search

    # No project on the instance answers to any hashtag a spec here asks
    # for, so `hashtag_conflicts` finds nothing and nothing is refused
    phab.project.search.return_value = {"data": []}

    task_ids = iter(range(1, 100))
    project_ids = iter(range(100, 200))

    def create_task(transactions):
        task_id = next(task_ids)
        return {"object": {"id": task_id, "phid": f"PHID-TASK-{task_id}"}}

    def create_project(transactions):
        project_id = next(project_ids)
        return {"object": {"id": project_id, "phid": f"PHID-PROJ-{project_id}"}}

    phab.maniphest.edit.side_effect = create_task
    phab.project.edit.side_effect = create_project

    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()

    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}

    return maniphest


def _spec(body):
    return Spec.from_data({"kind": "create", **body}, kind="create").render()


def _plan(phab, body):
    return plan_create(_maniphest(phab), _spec(body))


def _problems(body):
    """Every offline problem, with no app and therefore no instance."""
    return _spec(body).validate_offline()


def _codes(body):
    return [one.code for one in _problems(body)]


def _sent(phab):
    """Every call made to either `edit` endpoint, in the order it was made."""
    return [
        (name, call.kwargs.get("transactions") or call.args[0])
        for name, call in (
            [("project", one) for one in phab.project.edit.call_args_list]
            + [("task", one) for one in phab.maniphest.edit.call_args_list]
        )
    ]


# --------------------------------------------------------------------------
# 1. Offline: what a file can answer, answered without a server
# --------------------------------------------------------------------------


class TestTheOfflineChecks:
    """An unknown id, a duplicate id and a cycle need no instance at all."""

    def test_an_unknown_local_id_is_reported(self):
        codes = _codes({"tasks": [{"title": "Child", "parents": ["$epic"]}]})

        assert codes == ["unknown-local-id"]

    def test_it_says_what_the_spec_does_declare(self):
        problems = _problems(
            {
                "tasks": [
                    {"id": "epic", "title": "Epic"},
                    {"title": "Child", "parents": ["$epics"]},
                ]
            }
        )

        assert "$epics" in problems[0].reason or "epic" in problems[0].reason

    def test_two_objects_with_the_same_id_are_reported(self):
        codes = _codes(
            {"tasks": [{"id": "epic", "title": "One"}, {"id": "epic", "title": "Two"}]}
        )

        assert codes == ["duplicate-local-id"]

    def test_a_project_and_a_task_may_not_share_an_id_either(self):
        """One namespace for the whole document, whatever a `$ref` points at."""
        codes = _codes(
            {
                "projects": [{"id": "platform", "name": "Platform"}],
                "tasks": [{"id": "platform", "title": "Bootstrap"}],
            }
        )

        assert codes == ["duplicate-local-id"]

    def test_a_cycle_is_reported_as_one_problem(self):
        problems = _problems(
            {
                "tasks": [
                    {"id": "a", "title": "A", "parents": ["$b"]},
                    {"id": "b", "title": "B", "parents": ["$a"]},
                ]
            }
        )

        assert [one.code for one in problems] == ["local-id-cycle"]
        assert "$a" in problems[0].value and "$b" in problems[0].value

    def test_a_longer_cycle_is_reported_too(self):
        codes = _codes(
            {
                "tasks": [
                    {"id": "a", "title": "A", "parents": ["$b"]},
                    {"id": "b", "title": "B", "parents": ["$c"]},
                    {"id": "c", "title": "C", "parents": ["$a"]},
                ]
            }
        )

        assert codes == ["local-id-cycle"]

    def test_a_ref_to_a_grouping_that_creates_nothing_is_refused(self):
        """Otherwise the run stops halfway, with real objects already written.

        `id:` on a bare container is a declared local id, so nothing else
        reports it: the ordering puts the container first quite happily, the
        apply skips it because it creates nothing, and the `$ref` two items
        later finds there is no PHID - after the tasks before it exist.
        """
        codes = _codes(
            {
                "tasks": [
                    {"id": "epic", "tasks": [{"title": "Child"}]},
                    {"title": "Other", "parents": ["$epic"]},
                ]
            }
        )

        assert codes == ["not-creatable"]

    def test_a_ref_to_an_anchor_naming_one_existing_task_is_fine(self):
        """An anchor *is* one object: its children hang off what it names."""
        assert (
            _codes(
                {
                    "tasks": [
                        {"id": "epic", "parent": "T7", "tasks": [{"title": "Child"}]},
                        {"title": "Other", "parents": ["$epic"]},
                    ]
                }
            )
            == []
        )

    def test_a_ref_to_an_anchor_naming_two_tasks_is_refused(self):
        """Two objects are not one object, and a `$ref` names one."""
        codes = _codes(
            {
                "tasks": [
                    {"id": "epic", "parents": ["T7", "T8"]},
                    {"title": "Other", "parents": ["$epic"]},
                ]
            }
        )

        assert codes == ["not-creatable"]

    def test_an_id_on_a_grouping_nothing_points_at_is_left_alone(self):
        """Nothing is wrong with it until something tries to name it."""
        assert _codes({"tasks": [{"id": "epic", "tasks": [{"title": "Child"}]}]}) == []

    def test_a_nested_item_that_is_not_a_mapping_is_reported(self):
        """It used to be skipped by every walk, so a spec created one fewer."""
        codes = _codes({"tasks": [{"title": "Parent", "tasks": ["just a string"]}]})

        assert codes == ["wrong-type"]

    def test_a_local_id_is_not_a_name(self):
        """Two projects may be called the same thing and differ by `id:`."""
        assert (
            _codes(
                {
                    "projects": [
                        {"id": "one", "name": "Platform"},
                        {"id": "two", "name": "Platform"},
                    ],
                    "tasks": [{"title": "Bootstrap", "projects": ["$two"]}],
                }
            )
            == []
        )


class TestOfflineWithNoTokenAtAll:
    """Out of process, because in process everything is already imported."""

    def _run(self, body):
        script = (
            "import json, sys\n"
            "from phabfive.spec import Spec\n"
            f"body = json.loads({json.dumps(json.dumps(body))})\n"
            "spec = Spec.from_data(body, kind='create').render()\n"
            "codes = [one.code for one in spec.validate_offline()]\n"
            "forbidden = [name for name in "
            "('phabricator', 'requests', 'phabfive.core', 'phabfive.cli') "
            "if name in sys.modules]\n"
            "print(json.dumps({'codes': codes, 'forbidden': forbidden}))\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            env=self._environment(),
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

        return json.loads(result.stdout)

    @staticmethod
    def _environment():
        """As little environment as an interpreter needs to start.

        Copied from `tests/test_spec_isolation.py`, which owns the
        explanation: `PHAB_URL` and `PHAB_TOKEN` are unset and every place a
        configuration could come from points at a directory that does not
        exist - but is still *defined*, because `phabricator/__init__.py`
        reads `ProgramData` and `AppData` as it imports and an absent one is
        a `KeyError` on Windows rather than an empty directory.
        """
        keep = ("PATH", "SYSTEMROOT", "LD_LIBRARY_PATH", "VIRTUAL_ENV")
        environment = {name: os.environ[name] for name in keep if name in os.environ}

        nowhere = str(Path(os.sep, "nonexistent"))

        environment["HOME"] = nowhere
        environment["USERPROFILE"] = nowhere
        environment["APPDATA"] = nowhere
        environment["LOCALAPPDATA"] = nowhere
        environment["ProgramData"] = nowhere
        environment["ALLUSERSPROFILE"] = nowhere
        environment["PYTHONPATH"] = str(REPOSITORY)
        environment["TERM"] = "dumb"

        assert "PHAB_URL" not in environment
        assert "PHAB_TOKEN" not in environment

        return environment

    @pytest.mark.parametrize(
        "body, code",
        [
            (
                {"tasks": [{"title": "Child", "parents": ["$epic"]}]},
                "unknown-local-id",
            ),
            (
                {
                    "tasks": [
                        {"id": "epic", "title": "One"},
                        {"id": "epic", "title": "Two"},
                    ]
                },
                "duplicate-local-id",
            ),
            (
                {
                    "tasks": [
                        {"id": "a", "title": "A", "parents": ["$b"]},
                        {"id": "b", "title": "B", "parents": ["$a"]},
                    ]
                },
                "local-id-cycle",
            ),
        ],
    )
    def test_it_is_reported_with_no_token_and_no_home(self, body, code):
        answer = self._run(body)

        assert answer["codes"] == [code]
        assert answer["forbidden"] == []


class TestThePlanRefusesWhatOfflineFound:
    """`plan_create` raises rather than building half a document."""

    def test_an_unknown_local_id_stops_the_plan(self):
        phab = _phab()

        with pytest.raises(PhabfiveDataException, match=r"\$epic"):
            _plan(phab, {"tasks": [{"title": "Child", "parents": ["$epic"]}]})

        phab.maniphest.edit.assert_not_called()
        phab.project.edit.assert_not_called()


# --------------------------------------------------------------------------
# 2. Order: what a `$ref` names is created first
# --------------------------------------------------------------------------


class TestTheOrder:
    def test_a_project_the_spec_creates_comes_before_the_task_tagged_into_it(self):
        """The motivating spec of #483, written the way the issue writes it."""
        plan = _plan(
            _phab(),
            {
                "projects": [{"id": "platform", "name": "Platform"}],
                "tasks": [{"title": "Bootstrap", "projects": ["$platform"]}],
            },
        )

        assert [item.path for item in plan.items] == ["projects[0]", "tasks[0]"]
        assert [item.object_type for item in plan.items] == ["project", "task"]

    def test_the_document_order_of_the_sections_does_not_decide_it(self):
        """Two sections with nothing between them, written each way round.

        The section walk follows `CREATE_OBJECT_KEYS` and never the body's
        own key order, so `tasks:` comes first whichever key the file
        happens to open with. That is structural rather than something the
        Kahn pass provides, which is why the two items here are
        deliberately *unrelated*: with a `$ref` between them the ordering
        would fix the result either way and the property would go
        unpinned - which is how the previous version of this test came to
        be a second copy of the one above it.
        """
        projects_first = _plan(
            _phab(),
            {
                "projects": [{"name": "Platform"}],
                "tasks": [{"title": "Bootstrap"}],
            },
        )
        tasks_first = _plan(
            _phab(),
            {
                "tasks": [{"title": "Bootstrap"}],
                "projects": [{"name": "Platform"}],
            },
        )

        assert (
            [item.path for item in projects_first.items]
            == [item.path for item in tasks_first.items]
            == ["tasks[0]", "projects[0]"]
        )

    def test_an_unreferenced_project_keeps_its_place_in_document_order(self):
        """Ties are broken by document order, so the order is testable."""
        plan = _plan(
            _phab(),
            {
                "tasks": [{"title": "Bootstrap"}],
                "projects": [{"name": "Platform"}],
            },
        )

        assert [item.path for item in plan.items] == ["tasks[0]", "projects[0]"]

    def test_a_task_that_names_another_task_waits_for_it(self):
        plan = _plan(
            _phab(),
            {
                "tasks": [
                    {"title": "Child", "parents": ["$epic"]},
                    {"id": "epic", "title": "Epic"},
                ]
            },
        )

        assert [item.display["title"] for item in plan.items] == ["Epic", "Child"]

    def test_a_chain_of_three_is_ordered_all_the_way_down(self):
        plan = _plan(
            _phab(),
            {
                "tasks": [
                    {"id": "c", "title": "C", "parents": ["$b"]},
                    {"id": "b", "title": "B", "parents": ["$a"]},
                    {"id": "a", "title": "A"},
                ]
            },
        )

        assert [item.display["title"] for item in plan.items] == ["A", "B", "C"]

    def test_the_two_kinds_of_reference_mix_in_one_list(self):
        """`parents: ["T7", "$epic"]` - one exists, one does not yet."""
        plan = _plan(
            _phab(),
            {
                "tasks": [
                    {"title": "Child", "parents": ["T7", "$epic"]},
                    {"id": "epic", "title": "Epic"},
                ]
            },
        )

        assert [item.display["title"] for item in plan.items] == ["Epic", "Child"]

        child = plan.by_path()["tasks[0]"]
        parents = [
            one["value"] for one in child.transactions if one["type"] == "parents.add"
        ]

        assert parents == [["PHID-TASK-7", "$epic"]]


# --------------------------------------------------------------------------
# 3. Substitution: the literal becomes a PHID as the run goes
# --------------------------------------------------------------------------


class TestTheSubstitution:
    def test_the_plan_holds_the_reference_as_written(self):
        """There is no PHID yet, and a placeholder object would not survive JSON."""
        plan = _plan(
            _phab(),
            {
                "projects": [{"id": "platform", "name": "Platform"}],
                "tasks": [{"title": "Bootstrap", "projects": ["$platform"]}],
            },
        )

        task = plan.by_path()["tasks[0]"]

        assert {"type": "projects.add", "value": ["$platform"]} in task.transactions
        assert json.dumps(plan.as_records())

    def test_applying_tags_the_task_into_the_project_it_created(self):
        phab = _phab()
        plan = _plan(
            phab,
            {
                "projects": [{"id": "platform", "name": "Platform"}],
                "tasks": [{"title": "Bootstrap", "projects": ["$platform"]}],
            },
        )

        records = list(apply_plan(_maniphest(phab), plan))

        assert [record.status for record in records] == ["created", "created"]
        assert [record.object_type for record in records] == ["project", "task"]

        created_project = records[0].phid
        sent = _sent(phab)

        assert [name for name, _ in sent] == ["project", "task"]
        assert {"type": "projects.add", "value": [created_project]} in sent[1][1]

    def test_nothing_that_could_discard_a_collection_is_ever_sent(self):
        """`.add` on every collection, on every object, and no object to edit."""
        phab = _phab()
        plan = _plan(
            phab,
            {
                "projects": [
                    {"id": "platform", "name": "Platform", "members": ["alice"]}
                ],
                "tasks": [
                    {
                        "title": "Bootstrap",
                        "projects": ["$platform"],
                        "subscribers": ["alice"],
                    }
                ],
            },
        )

        list(apply_plan(_maniphest(phab), plan))

        for _, transactions in _sent(phab):
            for one in transactions:
                assert not one["type"].endswith(".set")
                assert not one["type"].endswith(".remove")

        for call in (
            phab.project.edit.call_args_list + phab.maniphest.edit.call_args_list
        ):
            assert "objectIdentifier" not in call.kwargs

    def test_a_reference_is_substituted_wherever_it_is_written(self):
        """A `$ref` is one grammar: the same name in three different keys."""
        phab = _phab()
        plan = _plan(
            phab,
            {
                "projects": [{"id": "platform", "name": "Platform"}],
                "tasks": [
                    {"id": "epic", "title": "Epic", "projects": ["$platform"]},
                    {
                        "title": "Child",
                        "parents": ["$epic"],
                        "projects": ["$platform"],
                    },
                ],
            },
        )

        records = list(apply_plan(_maniphest(phab), plan))
        by_id = {record.local_id: record.phid for record in records}

        sent = _sent(phab)[-1][1]

        assert {"type": "parents.add", "value": [by_id["epic"]]} in sent
        assert {"type": "projects.add", "value": [by_id["platform"]]} in sent

    def test_two_projects_with_one_name_are_told_apart_by_their_ids(self):
        """A local id is not a name; this is the sentence the issue ends on."""
        phab = _phab()
        plan = _plan(
            phab,
            {
                "projects": [
                    {"id": "one", "name": "Platform"},
                    {"id": "two", "name": "Platform"},
                ],
                "tasks": [{"title": "Bootstrap", "projects": ["$two"]}],
            },
        )

        records = list(apply_plan(_maniphest(phab), plan))
        second = {record.local_id: record.phid for record in records}["two"]

        assert second == _sent(phab)[-1][1][-1]["value"][0]

    def test_a_local_reference_never_reaches_the_instance(self):
        """It names nothing the server has, so nothing is asked about it.

        The whole `projects:` key of this spec is one `$ref`, so the online
        pass has no project to ask about and makes no `project.query` at
        all - which is what "a local reference resolves during apply" means
        one layer down.
        """
        phab = _phab()

        _plan(
            phab,
            {
                "projects": [{"id": "platform", "name": "Platform"}],
                "tasks": [{"title": "Bootstrap", "projects": ["$platform"]}],
            },
        )

        phab.project.query.assert_not_called()


class TestTheProjectItself:
    """Enough of a project to tag a task into, built by the app's own builder."""

    def test_the_name_is_what_is_sent(self):
        plan = _plan(_phab(), {"projects": [{"id": "p", "name": "Platform"}]})

        assert plan.items[0].transactions == ({"type": "name", "value": "Platform"},)

    def test_a_project_with_no_name_creates_nothing_and_is_refused(self):
        with pytest.raises(PhabfiveDataException, match="name"):
            _plan(_phab(), {"projects": [{"id": "p", "description": "no name"}]})

    def test_a_taken_hashtag_is_refused_before_anything_is_written(self):
        """A project cannot be deleted through Conduit, so this is a check."""
        phab = _phab()
        phab.project.search.return_value = {
            "data": [
                {
                    "phid": "PHID-PROJ-backend",
                    "id": 1,
                    "fields": {"name": "Backend Team"},
                }
            ]
        }

        with pytest.raises(PhabfiveDataException, match="hashtag"):
            _plan(phab, {"projects": [{"name": "Backend Team"}]})

        phab.project.edit.assert_not_called()

    def test_the_hashtag_it_asked_for_is_what_the_record_reports(self):
        phab = _phab()
        plan = _plan(phab, {"projects": [{"name": "Platform", "slugs": ["platform"]}]})

        records = list(apply_plan(_maniphest(phab), plan))

        assert records[0].monogram == "#platform"


class TestARefNamesTheRightKindOfObject:
    """`$p` naming a project is not a task's parent, and it is caught offline.

    The most expensive mistake a create spec can make. A `$ref` of the
    wrong kind used to validate clean on both layers, plan, create the
    project - and then be refused by the instance when the task naming it
    was sent, leaving behind a project Conduit can neither delete nor
    archive. Which key names which kind is a question about the file, so it
    is settled with no token: `ReferenceField.creates` says it and
    `validate._check_local_id_targets` reads it.
    """

    def test_a_project_is_not_a_tasks_parent(self):
        body = {
            "projects": [{"id": "p", "name": "Platform"}],
            "tasks": [{"title": "Child", "parents": ["$p"]}],
        }

        assert _codes(body) == ["not-creatable"]
        assert "names a task" in _problems(body)[0].reason

    def test_nothing_is_created_for_it(self):
        """Offline, so not one request is made and nothing is written."""
        phab = _phab()

        with pytest.raises(PhabfiveDataException):
            _plan(
                phab,
                {
                    "projects": [{"id": "p", "name": "Platform"}],
                    "tasks": [{"title": "Child", "parents": ["$p"]}],
                },
            )

        phab.project.edit.assert_not_called()
        phab.maniphest.edit.assert_not_called()

    def test_a_task_is_not_a_projects_parent(self):
        body = {
            "tasks": [{"id": "t", "title": "Epic"}],
            "projects": [{"name": "Platform", "parent": "$t"}],
        }

        assert _codes(body) == ["not-creatable"]

    def test_nothing_a_spec_creates_is_a_subscriber(self):
        """A spec creates no users, so a `$ref` in `subscribers:` names none."""
        body = {
            "projects": [{"id": "p", "name": "Platform"}],
            "tasks": [{"title": "Child", "subscribers": ["$p"]}],
        }

        assert _codes(body) == ["not-creatable"]
        assert "names nothing a spec creates" in _problems(body)[0].reason

    def test_a_project_is_a_projects_parent(self):
        body = {
            "projects": [
                {"id": "p", "name": "Platform"},
                {"name": "Platform Web", "parent": "$p"},
            ],
        }

        assert _codes(body) == []

    def test_a_task_is_a_tasks_parent(self):
        body = {
            "tasks": [
                {"id": "epic", "title": "Epic"},
                {"title": "Child", "parents": ["$epic"]},
            ],
        }

        assert _codes(body) == []

    def test_an_anchor_is_judged_by_what_it_points_at_not_by_its_section(self):
        """An anchor stands for an existing object; layer 2 asks what it is."""
        body = {
            "tasks": [
                {"id": "epic", "parent": "T7", "tasks": [{"title": "Child"}]},
                {"title": "Other", "parents": ["$epic"]},
            ],
        }

        assert _codes(body) == []


class TestAPolicyMayNameAProjectTheSpecCreates:
    """`visible-to: $platform`, which the module docstring has always claimed.

    `phabfive.policy` is shared with every `--visible-to` flag there is and
    a `$local-id` means nothing on a command line, so the sixth spelling is
    admitted by `validate._check_policy` rather than by the shared grammar.
    Before this the docstring said a `$local-id` passed through untouched
    and the offline pass refused it as `bad-policy`, so the branch was
    unreachable.
    """

    BODY = {
        "projects": [{"id": "platform", "name": "Platform"}],
        "tasks": [{"title": "Secret", "visible-to": "$platform"}],
    }

    def test_it_is_not_a_bad_policy(self):
        assert _codes(self.BODY) == []

    def test_the_task_is_restricted_to_the_project_that_was_created(self):
        phab = _phab()
        report = apply_plan(_maniphest(phab), _plan(phab, self.BODY))
        list(report)

        sent = dict(
            (one["type"], one["value"])
            for call in phab.maniphest.edit.call_args_list
            for one in call.kwargs["transactions"]
        )

        assert sent["view"] == "PHID-PROJ-100"

    def test_a_project_may_be_restricted_to_one_the_spec_creates(self):
        body = {
            "projects": [
                {"id": "platform", "name": "Platform"},
                {"name": "Secret", "visible-to": "$platform"},
            ],
        }

        assert _codes(body) == []

    def test_a_policy_naming_nothing_is_still_reported(self):
        body = {"tasks": [{"title": "Secret", "visible-to": "$nope"}]}

        assert _codes(body) == ["unknown-local-id"]

    def test_a_policy_naming_a_grouping_is_still_reported(self):
        body = {
            "tasks": [
                {"id": "group", "tasks": [{"title": "Child"}]},
                {"title": "Secret", "visible-to": "$group"},
            ],
        }

        assert _codes(body) == ["not-creatable"]
