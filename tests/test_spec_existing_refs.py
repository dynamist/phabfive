# -*- coding: utf-8 -*-

"""Pointing a create spec at objects that already exist (#482).

Two things, and the second is why the first is safe.

**One grammar.** Every key that names an object takes the same spellings for
something that already exists - a monogram, a `#hashtag`, an `@user`, a PHID
or a bare name - and each one resolves through the same Phase 2 layer-2
resolver, in one request for the whole document rather than one per value.

**The safety rule.** A create spec may *anchor* to an existing object, and
must never `.set` a collection field on one::

    tasks:
      - parent: T123            # existing: read for its PHID, never written
        tasks:
          - title: "Subtask A"
          - title: "Subtask B"

`subtasks.set` on T123 would silently discard every subtask T123 already
has. Nothing here sends one: an anchor is never the object of an `edit`
call at all, and the children carry `parents.add` instead.
`TestAnchoringNeverDiscardsExistingSubtasks` is the regression test that
would catch a `.set` creeping back - revert `parents.add` to `parents.set`
in `phabfive/spec/create.py` and it fails.

The app is mocked the way `tests/test_spec_create.py` does it:
`Phabfive.__init__` is faked, `.phab` is a `MagicMock`, and a mock that
fails raises a phabfive exception type - never `phabricator.APIError`,
which only `phabfive/conduit.py` ever sees.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import PhabfiveDataException
from phabfive.maniphest.core import Maniphest
from phabfive.spec import Spec
from phabfive.spec.create import apply_plan, plan_create
from phabfive.spec.online import TaskResolver

# --------------------------------------------------------------------------
# A fake instance: two projects, two users, one Space and two existing tasks
# --------------------------------------------------------------------------

PROJECTS = {
    "PHID-PROJ-backend": {"name": "Backend Team", "slugs": ["backend"]},
    "PHID-PROJ-sprint": {"name": "Sprint 42", "slugs": ["sprint"]},
    # Two projects share a name, which is what makes "Platform" ambiguous
    "PHID-PROJ-platform-a": {"name": "Platform", "slugs": ["platform-a"]},
    "PHID-PROJ-platform-b": {"name": "Platform", "slugs": ["platform-b"]},
}

USERS = {"alice": "PHID-USER-alice", "bob": "PHID-USER-bob"}

SPACES = {"S1": {"name": "Default", "fullName": "Default"}}

#: The existing tasks, and the subtasks T123 already has. Nothing in the
#: create path reads that list - that is the point - but it is here so the
#: fixture describes the instance the regression test is about.
TASKS = {
    123: {"phid": "PHID-TASK-123", "subtasks": ["PHID-TASK-900", "PHID-TASK-901"]},
    7: {"phid": "PHID-TASK-7", "subtasks": []},
}


def _phab():
    """A client that knows the instance above and hands out task ids."""
    phab = MagicMock()

    def project_query(limit=100, offset=0):
        page = list(PROJECTS.items())[offset : offset + limit]
        return {"data": dict(page)}

    def project_search(constraints=None, **kwargs):
        constraints = constraints or {}
        wanted = set(constraints.get("phids", []))
        return {
            "data": [
                {"phid": phid, "id": index, "fields": {"name": record["name"]}}
                for index, (phid, record) in enumerate(PROJECTS.items(), start=1)
                if not wanted or phid in wanted
            ]
        }

    def user_search(constraints=None, **kwargs):
        constraints = constraints or {}
        names = {one.casefold() for one in constraints.get("usernames", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                {"phid": phid, "fields": {"username": name}}
                for name, phid in USERS.items()
                if name in names or phid in phids
            ]
        }

    def phid_lookup(names):
        return {
            name: {**SPACES[name], "phid": f"PHID-SPCE-{name}"}
            for name in names
            if name in SPACES
        }

    def task_search(constraints=None, **kwargs):
        constraints = constraints or {}
        ids = {int(one) for one in constraints.get("ids", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                {"id": task_id, "phid": record["phid"]}
                for task_id, record in TASKS.items()
                if task_id in ids or record["phid"] in phids
            ]
        }

    ids = iter(range(1000, 1100))

    def edit(transactions):
        task_id = next(ids)
        return {"object": {"id": task_id, "phid": f"PHID-TASK-{task_id}"}}

    phab.project.query.side_effect = project_query
    phab.project.search.side_effect = project_search
    phab.user.search.side_effect = user_search
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller", "userName": "caller"}
    phab.phid.lookup.side_effect = phid_lookup
    phab.maniphest.search.side_effect = task_search
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


def _sent(phab):
    """Every transaction list `maniphest.edit` was called with, in order."""
    return [call.kwargs["transactions"] for call in phab.maniphest.edit.call_args_list]


def _by_title(phab):
    """What was sent, keyed by the title each call carried."""
    sent = {}

    for transactions in _sent(phab):
        title = next(
            (one["value"] for one in transactions if one["type"] == "title"), None
        )
        sent[title] = transactions

    return sent


# --------------------------------------------------------------------------
# The regression test the issue is about
# --------------------------------------------------------------------------


class TestAnchoringNeverDiscardsExistingSubtasks:
    """T123 has two subtasks. Anchoring to it must leave both in place."""

    ANCHORED = {
        "tasks": [
            {
                "parent": "T123",
                "tasks": [{"title": "Subtask A"}, {"title": "Subtask B"}],
            }
        ]
    }

    def test_anchoring_to_a_task_that_has_subtasks_keeps_every_one_of_them(self):
        """The whole issue in one test.

        Asserted over the *whole* recorded call list rather than as "the
        anchor was not edited", which would pass against code that never
        made that call in the first place and so would prove nothing.

        What keeps T123's two subtasks is that nothing is sent for T123 at
        all: no `subtasks.set`, no `subtasks.add`, no `objectIdentifier`.
        Each child names T123 in a `parents.add` of its own instead, which
        is a transaction on the child.
        """
        phab = _phab()

        plan = _plan(phab, self.ANCHORED)
        records = list(apply_plan(_maniphest(phab), plan))

        # Two children created, and nothing else: the anchor is not an edit
        assert [record.title for record in records] == ["Subtask A", "Subtask B"]
        assert phab.maniphest.edit.call_count == 2

        for call in phab.maniphest.edit.call_args_list:
            # The strongest form of the rule: a create spec has no code
            # path that edits an existing object
            assert "objectIdentifier" not in call.kwargs
            assert call.args == ()

        for transactions in _sent(phab):
            for transaction in transactions:
                assert not transaction["type"].endswith(".set")
                assert not transaction["type"].endswith(".remove")

            # Each child hangs off T123, and says so on itself
            assert {
                "type": "parents.add",
                "value": ["PHID-TASK-123"],
            } in transactions

        # Nothing anywhere claimed to know what T123 holds: the anchor's own
        # subtask list is never read back, so it can never be written back
        assert all(
            "attachments" not in call.kwargs
            for call in phab.maniphest.search.call_args_list
        )
        assert not any(
            "subtasks" in transaction["type"]
            for transactions in _sent(phab)
            for transaction in transactions
        )

    def test_the_anchor_is_an_item_that_creates_nothing(self):
        """It is in the plan - the dry run shows it - and it is never sent."""
        phab = _phab()

        plan = _plan(phab, self.ANCHORED)
        anchor = plan.by_path()["tasks[0]"]

        assert anchor.anchor is True
        assert anchor.creates is False
        assert anchor.transactions == ()
        assert anchor.phids == ("PHID-TASK-123",)
        assert plan.counts() == {"task": 2}

    def test_the_flat_form_is_the_same_thing(self):
        """`- title: X` + `parents: [T123]` compiles to the same transaction."""
        phab = _phab()

        plan = _plan(phab, {"tasks": [{"title": "Subtask A", "parents": ["T123"]}]})
        list(apply_plan(_maniphest(phab), plan))

        assert {"type": "parents.add", "value": ["PHID-TASK-123"]} in (
            _by_title(phab)["Subtask A"]
        )

    def test_an_anchor_may_name_several_objects(self):
        """`parents:` is plural and means it: the children hang off both."""
        phab = _phab()

        plan = _plan(
            phab,
            {"tasks": [{"parents": ["T123", "T7"], "tasks": [{"title": "Sub"}]}]},
        )
        list(apply_plan(_maniphest(phab), plan))

        assert {
            "type": "parents.add",
            "value": ["PHID-TASK-123", "PHID-TASK-7"],
        } in _by_title(phab)["Sub"]

    def test_an_anchor_may_be_something_the_spec_itself_creates(self):
        """`parent: $epic` waits for the epic and hangs the children off it."""
        phab = _phab()

        plan = _plan(
            phab,
            {
                "tasks": [
                    {"title": "Epic", "id": "epic"},
                    {"parent": "$epic", "tasks": [{"title": "Sub"}]},
                ]
            },
        )
        list(apply_plan(_maniphest(phab), plan))

        epic = _by_title(phab)["Epic"]
        assert all(not one["type"].endswith(".set") for one in epic)
        assert {"type": "parents.add", "value": ["PHID-TASK-1000"]} in (
            _by_title(phab)["Sub"]
        )

    def test_a_bare_container_still_links_its_children_to_nothing(self):
        """A titleless grouping that names nothing is not an anchor to anything.

        What a template writing `- tasks: [...]` has always meant, kept: the
        children are created and hang off nothing at all.
        """
        phab = _phab()

        plan = _plan(phab, {"tasks": [{"tasks": [{"title": "Sub"}]}]})
        list(apply_plan(_maniphest(phab), plan))

        assert all(one["type"] != "parents.add" for one in _by_title(phab)["Sub"])


class TestANonExistentAnchorCreatesNothing:
    """Layer 2 earns its keep here: the failure is before the first write."""

    def test_an_anchor_that_does_not_exist_refuses_the_whole_spec(self):
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(
                phab,
                {
                    "tasks": [
                        {
                            "parent": "T404",
                            "tasks": [{"title": "A"}, {"title": "B"}],
                        }
                    ]
                },
            )

        assert "T404" in str(excinfo.value)
        phab.maniphest.edit.assert_not_called()

    def test_an_anchor_named_by_a_phid_that_does_not_exist_is_refused(self):
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(
                phab,
                {"tasks": [{"parent": "PHID-TASK-nope", "tasks": [{"title": "A"}]}]},
            )

        assert "PHID-TASK-nope" in str(excinfo.value)
        phab.maniphest.edit.assert_not_called()


# --------------------------------------------------------------------------
# One grammar, in every field that names an object
# --------------------------------------------------------------------------


class TestEverySpellingResolves:
    """The five spellings of "something that already exists", per field.

    Each field takes every spelling that can name the kind of object it
    holds. A task has no name to be looked up by - two tasks are very often
    called the same thing - so `parents:` takes the monogram and the PHID,
    and a bare word in it is a typo rather than a task.
    """

    @pytest.mark.parametrize(
        "field, value, expected",
        [
            # A task: by monogram, and by PHID
            ("parent", "T123", {"type": "parents.add", "value": ["PHID-TASK-123"]}),
            (
                "parent",
                "PHID-TASK-123",
                {"type": "parents.add", "value": ["PHID-TASK-123"]},
            ),
            (
                "parents",
                ["T7"],
                {"type": "parents.add", "value": ["PHID-TASK-7"]},
            ),
            (
                "subtasks",
                ["T7"],
                {"type": "subtasks.add", "value": ["PHID-TASK-7"]},
            ),
            # A project: by hashtag, by name and by PHID
            (
                "projects",
                ["#backend"],
                {"type": "projects.add", "value": ["PHID-PROJ-backend"]},
            ),
            (
                "projects",
                ["Backend Team"],
                {"type": "projects.add", "value": ["PHID-PROJ-backend"]},
            ),
            (
                "projects",
                ["PHID-PROJ-backend"],
                {"type": "projects.add", "value": ["PHID-PROJ-backend"]},
            ),
            # A user: by @name, by name, by PHID, and the @me keyword
            ("assignment", "@alice", {"type": "owner", "value": "PHID-USER-alice"}),
            ("assignment", "alice", {"type": "owner", "value": "PHID-USER-alice"}),
            (
                "assignment",
                "PHID-USER-alice",
                {"type": "owner", "value": "PHID-USER-alice"},
            ),
            ("assignment", "@me", {"type": "owner", "value": "PHID-USER-caller"}),
            (
                "subscribers",
                ["@alice", "bob"],
                {
                    "type": "subscribers.add",
                    "value": ["PHID-USER-alice", "PHID-USER-bob"],
                },
            ),
            # A Space: by monogram, by name and by PHID
            ("space", "S1", {"type": "space", "value": "PHID-SPCE-S1"}),
            ("space", "Default", {"type": "space", "value": "PHID-SPCE-S1"}),
            ("space", "PHID-SPCE-S1", {"type": "space", "value": "PHID-SPCE-S1"}),
        ],
    )
    def test_a_spelling_reaches_the_transaction_as_a_phid(self, field, value, expected):
        phab = _phab()

        plan = _plan(phab, {"tasks": [{"title": "Task", field: value}]})

        assert expected in plan.items[0].transactions

    @pytest.mark.parametrize("key", ["parent", "parents", "subtasks"])
    def test_a_key_that_names_tasks_refuses_a_project_or_a_user(self, key):
        """`#backend` in `parents:` is a typo, and is named as one offline."""
        phab = _phab()
        value = "#backend" if key != "parents" else ["#backend"]

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(phab, {"tasks": [{"title": "Task", key: value}]})

        assert "#backend" in str(excinfo.value)
        phab.maniphest.search.assert_not_called()

    def test_an_ambiguous_bare_name_is_refused_naming_the_candidates(self):
        """Two projects called "Platform": neither is silently picked."""
        phab = _phab()

        with pytest.raises(PhabfiveDataException) as excinfo:
            _plan(phab, {"tasks": [{"title": "Task", "projects": ["Platform"]}]})

        message = str(excinfo.value)

        assert "ambiguous" in message.casefold()
        assert message.count("Platform") >= 2
        phab.maniphest.edit.assert_not_called()


class TestReferencesAreResolvedInBulk:
    """One request per kind for the whole document, not one per value."""

    def test_every_task_in_the_spec_is_asked_about_in_one_search(self):
        """It used to be one `maniphest.search` per parent and per subtask."""
        phab = _phab()

        _plan(
            phab,
            {
                "tasks": [
                    {"title": "One", "parents": ["T123"]},
                    {"title": "Two", "parent": "T7", "subtasks": ["T123"]},
                    {"parent": "T123", "tasks": [{"title": "Three"}]},
                ]
            },
        )

        assert phab.maniphest.search.call_count == 1
        assert phab.maniphest.search.call_args.kwargs["constraints"] == {
            "ids": [7, 123]
        }

    def test_a_phid_costs_a_second_request_and_only_when_one_is_written(self):
        """Conduit ANDs its constraints, so ids and phids cannot share a call."""
        phab = _phab()

        _plan(
            phab,
            {
                "tasks": [
                    {"title": "One", "parents": ["T7"]},
                    {"title": "Two", "parents": ["PHID-TASK-123"]},
                ]
            },
        )

        assert phab.maniphest.search.call_count == 2
        assert [
            call.kwargs["constraints"] for call in phab.maniphest.search.call_args_list
        ] == [{"ids": [7]}, {"phids": ["PHID-TASK-123"]}]


class TestTheTaskResolver:
    """What `validate_online` answers for a monogram, now that it answers."""

    def test_it_is_one_of_the_defaults(self):
        from phabfive.spec.online import DEFAULT_RESOLVERS

        assert any(isinstance(resolver, TaskResolver) for resolver in DEFAULT_RESOLVERS)

    def test_a_task_that_exists_answers_with_its_phid_and_its_monogram(self):
        phab = _phab()

        answers = TaskResolver().resolve(_maniphest(phab), ["T123"])

        assert answers["T123"].resolved is True
        assert answers["T123"].phid == "PHID-TASK-123"
        assert answers["T123"].name == "T123"

    def test_a_phid_is_labelled_by_the_monogram_a_reader_would_write(self):
        phab = _phab()

        answers = TaskResolver().resolve(_maniphest(phab), ["PHID-TASK-123"])

        assert answers["PHID-TASK-123"].phid == "PHID-TASK-123"
        assert answers["PHID-TASK-123"].name == "T123"

    def test_a_task_that_does_not_exist_is_a_failure_and_not_a_raise(self):
        phab = _phab()

        answers = TaskResolver().resolve(_maniphest(phab), ["T404"])

        assert answers["T404"].problem == "unknown-reference"
        assert "T404" in (answers["T404"].reason or "")

    def test_a_value_that_names_no_task_at_all_says_what_a_task_is_named_by(self):
        phab = _phab()

        answers = TaskResolver().resolve(_maniphest(phab), ["Fix the thing"])

        assert answers["Fix the thing"].problem == "bad-monogram"
        assert "T123" in (answers["Fix the thing"].reason or "")
        phab.maniphest.search.assert_not_called()

    def test_it_answers_for_every_value_it_is_given(self):
        """A value a resolver skips is reported by nobody; see `Resolver`."""
        phab = _phab()
        values = ["T123", "T404", "PHID-TASK-7", "nonsense"]

        answers = TaskResolver().resolve(_maniphest(phab), values)

        assert sorted(answers) == sorted(values)
