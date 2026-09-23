# -*- coding: utf-8 -*-

"""The constraints `maniphest.search` answers and phabfive used to ignore (#478).

`maniphest.search` accepts considerably more than phabfive sent. Eleven of
its constraints - `ids`, `phids`, `subscribers`, `subtypes`, `parentIDs`,
`subtaskIDs`, `hasParents`, `hasSubtasks`, `closerPHIDs`, `closedStart` and
`closedEnd` - were absent from the whole tree, and three more (`priorities`,
`columnPHIDs` and an explicit `statuses` list) were the difference between a
query the server answers and a full walk phabfive filters in Python.

Three things are pinned here, and they are not the same thing:

* that each constraint **reaches the request body**, under the name this
  endpoint gives it. A wrong name is ERR-INVALID-CONSTRAINT, not a wrong
  result, so the name is the test.
* that a filter which moved from the client to the server **finds the same
  tasks in the same order**. Each of those has a test running the search
  both ways over one dataset: once with the server honouring the constraint,
  once with it ignoring it, asserting the two answers are identical. A
  constraint that narrows what is fetched but not what matches is the only
  kind of move that is safe, and this is what says so.
* that an instance which does not know a constraint is answered by name
  rather than with Conduit's own code (#434), and that a constraint sent
  only as an optimisation is dropped and asked again without.

The paging is `phabfive.pagination`'s, which maniphest was the last app not
to use.
"""

import contextlib
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import phabfive.pagination
from phabfive.cli.maniphest import maniphest_app
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveDataException,
    PhabfiveInputException,
)
from phabfive.maniphest.core import Maniphest, current_state_targets
from phabfive.transitions import (
    parse_column_patterns,
    parse_priority_patterns,
    parse_status_patterns,
)

ADMIN_PHID = "PHID-USER-1234567890abcdefghij"
ALICE_PHID = "PHID-USER-aaaaaaaaaaaaaaaaaaaa"
BOARD = "PHID-PROJ-board"
BACKLOG = "PHID-PCOL-backlog"
DONE = "PHID-PCOL-done"

#: What maniphest.priority.search answers on a stock Phorge. The priority
#: lift reads *this* rather than `get_api_priority_map`'s constant, because a
#: constant cannot see an instance that relabelled a priority - note that
#: stock Phorge already calls 90 "Needs Triage" and the constant calls it
#: "Triage".
PRIORITIES = {
    "data": [
        {"name": "Unbreak Now!", "keywords": ["unbreak"], "value": 100},
        {"name": "Needs Triage", "keywords": ["triage"], "value": 90},
        {"name": "High", "keywords": ["high"], "value": 80},
        {"name": "Normal", "keywords": ["normal"], "value": 50},
        {"name": "Low", "keywords": ["low"], "value": 25},
        {"name": "Wishlist", "keywords": ["wish", "wishlist"], "value": 0},
    ]
}

#: What maniphest.querystatuses answers, as the three status-aware paths read it.
STATUSES = {
    "openStatuses": ["open"],
    "closedStatuses": {"0": "resolved", "1": "wontfix"},
    "statusMap": {"open": "Open", "resolved": "Resolved", "wontfix": "Wontfix"},
}


def _task(task_id, *, priority=("Normal", 50), status=("Open", "open"), column=None):
    """One task as maniphest.search answers with it."""
    task = {
        "id": task_id,
        "phid": f"PHID-TASK-{task_id}",
        "fields": {
            "name": f"Task {task_id}",
            "priority": {"name": priority[0], "value": priority[1]},
            "status": {"name": status[0], "value": status[1]},
        },
        "attachments": {"columns": {"boards": {}}},
    }

    if column is not None:
        task["attachments"]["columns"]["boards"] = {
            BOARD: {"columns": [{"phid": column}]}
        }

    return task


def _user_search(constraints):
    """A user.search that knows alice, and no user called "me"."""
    records = [{"phid": ALICE_PHID, "fields": {"username": "alice"}}]
    names = {name.casefold() for name in constraints.get("usernames", [])}
    phids = set(constraints.get("phids", []))

    return {
        "data": [
            record
            for record in records
            if record["fields"]["username"] in names or record["phid"] in phids
        ]
    }


def _page(data, after=None):
    """One maniphest.search response, in the shape the client hands back."""
    response = MagicMock()
    response.response = {"data": list(data), "cursor": {"after": after}}
    return response


def _maniphest(tasks=(), *, honour=(), pages=None):
    """A Maniphest whose maniphest.search is a fake instance.

    `honour` names the constraints the fake server actually applies. That is
    what makes a both-ways test possible: the same dataset is searched once
    by a server that filters and once by one that does not, and the answer
    has to be the same either way.

    `pages` replaces the dataset with a list of pages, so that cursor paging
    can be exercised on its own.
    """
    maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "https://phabricator.example.com"
    maniphest.conf = {"PHAB_SPACE": "S1", "PHAB_URL": "https://phab.example.com/api/"}

    maniphest.phab.phid.lookup.return_value = {
        "S1": {"phid": "PHID-SPCE-1", "name": "Global", "fullName": "Global"}
    }
    maniphest.phab.user.whoami.return_value = {"phid": ADMIN_PHID, "userName": "admin"}
    maniphest.phab.user.search.side_effect = _user_search
    maniphest.phab.maniphest.querystatuses.return_value = dict(STATUSES)
    maniphest.phab.maniphest.priority.search.return_value = dict(PRIORITIES)
    maniphest.phab.project.query.return_value = {"data": {}}
    maniphest.phab.project.column.search.return_value = {
        "data": [
            {"phid": BACKLOG, "fields": {"name": "Backlog", "sequence": 0}},
            {"phid": DONE, "fields": {"name": "Done", "sequence": 1}},
        ]
    }
    maniphest._resolve_project_phids = MagicMock(return_value=[BOARD])
    # in: conditions ask about current state, so no history is needed - but
    # the filtering loop fetches it anyway, and a MagicMock is not a dict.
    maniphest._fetch_all_transactions = MagicMock(
        return_value={"columns": [], "priority": [], "status": []}
    )

    dataset = list(tasks)

    def search(**kwargs):
        if pages is not None:
            after = kwargs.get("after")
            index = 0 if after is None else int(after)
            nxt = str(index + 1) if index + 1 < len(pages) else None
            return _page(pages[index], after=nxt)

        constraints = kwargs.get("constraints", {})
        data = dataset

        if "priorities" in honour and "priorities" in constraints:
            data = [
                task
                for task in data
                if task["fields"]["priority"]["value"] in constraints["priorities"]
            ]
        if "statuses" in honour and "statuses" in constraints:
            data = [
                task
                for task in data
                if task["fields"]["status"]["value"] in constraints["statuses"]
            ]
        if "columnPHIDs" in honour and "columnPHIDs" in constraints:
            wanted = set(constraints["columnPHIDs"])
            data = [
                task
                for task in data
                if wanted
                & {
                    column["phid"]
                    for board in task["attachments"]["columns"]["boards"].values()
                    for column in board.get("columns", [])
                }
            ]

        return _page(data)

    maniphest.phab.maniphest.search.side_effect = search

    return maniphest


def _constraints(maniphest, index=0):
    """Constraints of one maniphest.search call."""
    return maniphest.phab.maniphest.search.call_args_list[index][1]["constraints"]


def _run(maniphest, **kwargs):
    """task_search, answering with the ids it kept, in the order it kept them."""
    with patch.object(Maniphest, "_build_task_display_data") as build:
        maniphest.task_search(**kwargs)

    return [task["id"] for task in build.call_args[0][0]]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestEachConstraintReachesTheAPI:
    """One test per constraint, asserting the name and the value sent."""

    @pytest.mark.parametrize("key", ["ids", "parent", "subtask"])
    def test_a_bare_number_is_not_a_task_id(self, mock_init, key):
        """One grammar for every key that takes one (#463 gate).

        `--ids 307` used to be accepted while `--include 307` was refused,
        for the same kind of value on the same command - and
        `monograms=("T",)` in the registry, which the offline pass checks a
        spec against, says T123.
        """
        maniphest = _maniphest()

        with pytest.raises(PhabfiveInputException) as error:
            _run(maniphest, **{key: "307"})

        assert "Invalid task ID '307'" in str(error.value)
        assert "Expected format: T123" in str(error.value)

    def test_include_and_ids_refuse_the_same_value_the_same_way(self, mock_init):
        from phabfive.spec.search import task_ids

        with pytest.raises(PhabfiveInputException) as included:
            task_ids("307")

        with pytest.raises(PhabfiveInputException) as listed:
            task_ids("307", option="'ids'")

        assert str(included.value) == "Invalid task ID '307'. Expected format: T123"
        assert str(listed.value) == (
            "Invalid task ID '307' for 'ids'. Expected format: T123"
        )

    @pytest.mark.parametrize(
        "kwargs, constraint, value",
        [
            ({"ids": "T1,T2"}, "ids", [1, 2]),
            ({"ids": ["T1", "T2"]}, "ids", [1, 2]),
            ({"ids": ["T1,T2", "T3"]}, "ids", [1, 2, 3]),
            (
                {"phids": "PHID-TASK-a,PHID-TASK-b"},
                "phids",
                ["PHID-TASK-a", "PHID-TASK-b"],
            ),
            ({"subscriber": "alice"}, "subscribers", [ALICE_PHID]),
            ({"subscriber": "@me"}, "subscribers", [ADMIN_PHID]),
            ({"subtype": "bug,chore"}, "subtypes", ["bug", "chore"]),
            ({"parent": "T7"}, "parentIDs", [7]),
            ({"subtask": "T9,T10"}, "subtaskIDs", [9, 10]),
            ({"has_parents": True}, "hasParents", True),
            ({"has_parents": False}, "hasParents", False),
            ({"has_subtasks": True}, "hasSubtasks", True),
            ({"has_subtasks": False}, "hasSubtasks", False),
            ({"closed_by": "alice"}, "closerPHIDs", [ALICE_PHID]),
        ],
    )
    def test_the_constraint_is_sent(self, mock_init, kwargs, constraint, value):
        maniphest = _maniphest()

        _run(maniphest, **kwargs)

        assert _constraints(maniphest)[constraint] == value

    @pytest.mark.parametrize(
        "kwarg, constraint",
        [("closed_after", "closedStart"), ("closed_before", "closedEnd")],
    )
    def test_a_closed_time_is_sent_as_a_timestamp(self, mock_init, kwarg, constraint):
        maniphest = _maniphest()

        _run(maniphest, **{kwarg: "7d"})

        # A Unix timestamp, as createdStart and modifiedStart already are,
        # and not the "7d" a person wrote.
        assert isinstance(_constraints(maniphest)[constraint], int)
        assert _constraints(maniphest)[constraint] > 1_000_000_000

    def test_nothing_is_sent_for_a_constraint_nobody_asked_for(self, mock_init):
        maniphest = _maniphest()

        _run(maniphest, tag="proj")

        sent = set(_constraints(maniphest))
        assert (
            sent
            & {
                "ids",
                "phids",
                "subscribers",
                "subtypes",
                "parentIDs",
                "subtaskIDs",
                "hasParents",
                "hasSubtasks",
                "closerPHIDs",
                "closedStart",
                "closedEnd",
            }
            == set()
        )

    def test_a_false_boolean_is_still_a_search(self, mock_init):
        """`has-parents: false` asks for the tasks with no parent.

        Which is a filter, so it must lift the "no search criteria" guard
        that a bare `search` trips.
        """
        maniphest = _maniphest()

        _run(maniphest, has_parents=False)

        assert _constraints(maniphest)["hasParents"] is False

    @pytest.mark.parametrize("kwarg", ["ids", "parent", "subtask"])
    def test_something_that_is_not_a_task_is_refused_by_name(self, mock_init, kwarg):
        maniphest = _maniphest()

        with pytest.raises(PhabfiveInputException) as error:
            maniphest.task_search(**{kwarg: "P45"})

        assert "P45" in str(error.value)
        assert "T123" in str(error.value)

    @pytest.mark.parametrize("kwarg", ["ids", "parent", "subtask"])
    def test_and_refused_before_anything_is_fetched(self, mock_init, kwarg):
        maniphest = _maniphest()

        with pytest.raises(PhabfiveInputException):
            maniphest.task_search(**{kwarg: "nonsense"})

        assert maniphest.phab.maniphest.search.call_count == 0


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestTheConstraintsAreCombinedNotReplaced:
    def test_several_at_once_all_reach_the_body(self, mock_init):
        maniphest = _maniphest()

        _run(
            maniphest,
            subscriber="alice",
            subtype="bug",
            has_subtasks=True,
            closed_after="30d",
        )

        constraints = _constraints(maniphest)
        assert constraints["subscribers"] == [ALICE_PHID]
        assert constraints["subtypes"] == ["bug"]
        assert constraints["hasSubtasks"] is True
        assert "closedStart" in constraints

    def test_ids_is_not_include(self, mock_init):
        """`ids` narrows the search; `include` bypasses it.

        They are different words for different things, and sending one as
        the other would make `--include` a filter.
        """
        maniphest = _maniphest([_task(1), _task(2)])

        _run(maniphest, ids="T1", subtype="bug")

        assert _constraints(maniphest)["ids"] == [1]
        # --include is fetched separately, by id, after the filters ran.
        assert maniphest.phab.maniphest.search.call_count == 1


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestWhatATransitionFilterCanLift:
    """Only a condition about current state may become a constraint."""

    def test_an_in_condition_names_the_state(self, mock_init):
        patterns = parse_priority_patterns("in:High")

        assert current_state_targets(patterns, "priority") == ["High"]

    @pytest.mark.parametrize(
        "pattern", ["been:High", "from:High", "to:High", "raised", "not:in:High"]
    )
    def test_a_condition_about_the_past_lifts_nothing(self, mock_init, pattern):
        patterns = parse_priority_patterns(pattern)

        assert current_state_targets(patterns, "priority") is None

    def test_one_pattern_without_an_in_refuses_the_whole_lift(self, mock_init):
        """The patterns are ORed, so narrowing for one would drop the other's."""
        patterns = parse_priority_patterns("in:High,been:Low")

        assert current_state_targets(patterns, "priority") is None

    def test_an_in_beside_a_history_condition_still_lifts(self, mock_init):
        """A pattern's conditions are ANDed, so the in: still bounds it."""
        patterns = parse_priority_patterns("in:High+been:Low")

        assert current_state_targets(patterns, "priority") == ["High"]

    def test_every_pattern_contributes_its_own(self, mock_init):
        patterns = parse_priority_patterns("in:High,in:Low")

        assert current_state_targets(patterns, "priority") == ["High", "Low"]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestThePriorityLift:
    """--priority was entirely client-side: "high" fetched every task."""

    def test_an_in_pattern_becomes_the_priorities_constraint(self, mock_init):
        maniphest = _maniphest()

        _run(maniphest, priority_patterns=parse_priority_patterns("in:High"))

        assert _constraints(maniphest)["priorities"] == [80]

    def test_a_history_pattern_sends_none(self, mock_init):
        maniphest = _maniphest()

        _run(maniphest, priority_patterns=parse_priority_patterns("been:High"))

        assert "priorities" not in _constraints(maniphest)

    def test_the_registry_decides_what_may_be_lifted(self, mock_init):
        """`Field.lifts` is read, not documentation (#463 gate).

        Emptying the declaration has to really stop the narrowing, or the
        registry's claim to be the one place this rule lives is false and
        `current_state_targets` is the real one.
        """
        import dataclasses

        from phabfive.spec.registry import field_by_name

        maniphest = _maniphest()
        without = dataclasses.replace(
            field_by_name("priority", "task", "search"), lifts=()
        )

        with patch("phabfive.maniphest.core.field_by_name", return_value=without):
            _run(maniphest, priority_patterns=parse_priority_patterns("in:High"))

        assert "priorities" not in _constraints(maniphest)

    def test_a_priority_this_instance_never_named_sends_none(self, mock_init):
        """An instance that renamed a priority still gets the full walk.

        Lifting only the names that map would drop the tasks of the ones
        that did not, which is the silent result change this must not make.
        """
        maniphest = _maniphest()

        _run(maniphest, priority_patterns=parse_priority_patterns("in:Urgent"))

        assert "priorities" not in _constraints(maniphest)

    def test_a_relabelled_priority_is_lifted_by_this_instance_s_value(self, mock_init):
        """The table is the instance's, not the standard one (#463 gate).

        An instance is free to relabel a priority: Phorge itself calls 90
        "Needs Triage" while `get_api_priority_map` calls it "Triage". Here
        50 is called "High" and 80 is called "Urgent", which is exactly the
        case a constant table gets wrong - it would send `priorities: [80]`
        for `in:High`, fetch the Urgent tasks, and then let the client-side
        filter (which compares `fields.priority.name`) drop every one of
        them, answering a search that used to find tasks with nothing.
        """
        relabelled = {
            "data": [
                {"name": "Urgent", "keywords": ["urgent"], "value": 80},
                {"name": "High", "keywords": ["high"], "value": 50},
            ]
        }
        tasks = [_task(1, priority=("High", 50)), _task(2, priority=("Urgent", 80))]

        server_side = _maniphest(tasks, honour=("priorities",))
        server_side.phab.maniphest.priority.search.return_value = relabelled

        client_side = _maniphest(tasks)
        client_side.phab.maniphest.priority.search.return_value = relabelled
        client_side._lifted_priorities = MagicMock(return_value=None)

        lifted = _run(server_side, priority_patterns=parse_priority_patterns("in:High"))
        walked = _run(client_side, priority_patterns=parse_priority_patterns("in:High"))

        assert _constraints(server_side)["priorities"] == [50]
        assert lifted == walked == [1]

    def test_a_name_beats_another_priority_s_keyword(self, mock_init):
        """The filter compares display names, so a name has to win.

        Here "high" is the *keyword* of the priority called Urgent and the
        *name* of the one worth 50. Keying record by record would have made
        `in:High` send 80 and the client-side filter, which compares
        `fields.priority.name`, drop every task it fetched.
        """
        collided = {
            "data": [
                {"name": "Urgent", "keywords": ["high"], "value": 80},
                {"name": "High", "keywords": ["normal"], "value": 50},
            ]
        }
        maniphest = _maniphest()
        maniphest.phab.maniphest.priority.search.return_value = collided

        _run(maniphest, priority_patterns=parse_priority_patterns("in:High"))

        assert _constraints(maniphest)["priorities"] == [50]

    def test_an_instance_that_cannot_be_asked_is_not_narrowed(self, mock_init):
        """No priority table, no lift: a guess would change the result."""
        maniphest = _maniphest()
        maniphest.phab.maniphest.priority.search.side_effect = PhabfiveAPIException(
            "ERR-CONDUIT-CORE", "no such method"
        )

        _run(maniphest, priority_patterns=parse_priority_patterns("in:High"))

        assert "priorities" not in _constraints(maniphest)

    def test_both_ways_find_the_same_tasks(self, mock_init):
        """The move is safe only if the answer does not change."""
        tasks = [
            _task(1, priority=("High", 80)),
            _task(2, priority=("Normal", 50)),
            _task(3, priority=("High", 80)),
            _task(4, priority=("Wishlist", 0)),
        ]

        server_side = _maniphest(tasks, honour=("priorities",))
        client_side = _maniphest(tasks)
        client_side._lifted_priorities = MagicMock(return_value=None)

        patterns = parse_priority_patterns("in:High")
        lifted = _run(server_side, priority_patterns=patterns)
        walked = _run(client_side, priority_patterns=parse_priority_patterns("in:High"))

        # Same tasks, and in the same order: the order is decided after the
        # fetch, so a narrower fetch must not reshuffle it.
        assert lifted == walked
        assert sorted(lifted) == [1, 3]
        # And the two really did take different routes.
        assert _constraints(server_side)["priorities"] == [80]
        assert "priorities" not in _constraints(client_side)

    def test_the_server_narrows_and_the_filter_still_decides(self, mock_init):
        """A server that answers the constraint loosely changes nothing.

        The pattern is re-checked per task, so a task the constraint let
        through is still dropped by the filter - which is why lifting is
        allowed to be approximate and the filter is not.
        """
        tasks = [_task(1, priority=("High", 80)), _task(2, priority=("Low", 25))]
        maniphest = _maniphest(tasks)  # honours nothing: answers with both

        assert _run(
            maniphest, priority_patterns=parse_priority_patterns("in:High")
        ) == [1]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestTheStatusLift:
    def test_an_in_pattern_becomes_an_explicit_statuses_list(self, mock_init):
        maniphest = _maniphest()

        _run(maniphest, status_patterns=parse_status_patterns("any+in:Resolved"))

        assert _constraints(maniphest)["statuses"] == ["resolved"]

    def test_the_scope_still_bounds_it(self, mock_init):
        """`--status in:Resolved` reaches open tasks only, and still does.

        The scope is what decides which statuses are fetched; an in: that
        the scope cannot reach must not widen it, or a search that finds
        nothing today would start finding tasks.
        """
        maniphest = _maniphest()

        _run(maniphest, status_patterns=parse_status_patterns("in:Resolved"))

        assert _constraints(maniphest)["statuses"] == ["open"]

    def test_a_history_pattern_keeps_the_scope_list(self, mock_init):
        maniphest = _maniphest()

        _run(maniphest, status_patterns=parse_status_patterns("any+been:Resolved"))

        assert "statuses" not in _constraints(maniphest)

    def test_both_ways_find_the_same_tasks(self, mock_init):
        tasks = [
            _task(1, status=("Resolved", "resolved")),
            _task(2, status=("Open", "open")),
            _task(3, status=("Wontfix", "wontfix")),
        ]

        server_side = _maniphest(tasks, honour=("statuses",))
        client_side = _maniphest(tasks)
        client_side._lifted_statuses = MagicMock(return_value=None)

        lifted = _run(
            server_side, status_patterns=parse_status_patterns("any+in:Resolved")
        )
        walked = _run(
            client_side, status_patterns=parse_status_patterns("any+in:Resolved")
        )

        assert lifted == walked
        assert sorted(lifted) == [1]
        assert _constraints(server_side)["statuses"] == ["resolved"]
        assert "statuses" not in _constraints(client_side)


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestTheColumnLift:
    def test_an_in_pattern_becomes_the_column_phids_constraint(self, mock_init):
        maniphest = _maniphest()

        _run(
            maniphest,
            tag="proj",
            column_patterns=parse_column_patterns("in:Backlog"),
        )

        assert _constraints(maniphest)["columnPHIDs"] == [BACKLOG]

    def test_without_a_board_there_is_nothing_to_resolve_against(self, mock_init):
        """The boards come off each task when no --tag names one.

        Which is not known before the tasks are fetched, so there is nothing
        to narrow the fetch with.
        """
        maniphest = _maniphest()

        _run(maniphest, column_patterns=parse_column_patterns("in:Backlog"))

        assert "columnPHIDs" not in _constraints(maniphest)

    def test_a_column_the_board_does_not_have_sends_none(self, mock_init):
        maniphest = _maniphest()

        _run(maniphest, tag="proj", column_patterns=parse_column_patterns("in:Nowhere"))

        assert "columnPHIDs" not in _constraints(maniphest)

    def test_both_ways_find_the_same_tasks(self, mock_init):
        tasks = [
            _task(1, column=BACKLOG),
            _task(2, column=DONE),
            _task(3, column=BACKLOG),
        ]

        server_side = _maniphest(tasks, honour=("columnPHIDs",))
        client_side = _maniphest(tasks)
        client_side._lifted_column_phids = MagicMock(return_value=None)

        patterns = parse_column_patterns("in:Backlog")
        lifted = _run(server_side, tag="proj", column_patterns=patterns)
        walked = _run(
            client_side, tag="proj", column_patterns=parse_column_patterns("in:Backlog")
        )

        assert lifted == walked
        assert sorted(lifted) == [1, 3]
        assert _constraints(server_side)["columnPHIDs"] == [BACKLOG]
        assert "columnPHIDs" not in _constraints(client_side)


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestAnInstanceThatDoesNotKnowAConstraint:
    """#434: Phabricator and Phorge do not answer the same constraints."""

    def _refuse(self, maniphest, constraint, *, then=None):
        """A server that refuses one constraint, the way a real one does.

        The message is Phorge's own, verbatim off the local instance: it
        names **nothing**. A fake that named the offending key - as this
        file's first draft did - lets phabfive read the culprit out of the
        sentence, which is exactly the thing no real instance offers.
        """
        calls = []

        def search(**kwargs):
            calls.append(kwargs)
            if constraint in kwargs.get("constraints", {}):
                raise PhabfiveAPIException(
                    "ERR-INVALID-CONSTRAINT",
                    'Parameter "constraints" includes an invalid key.',
                )
            return _page(then or [])

        maniphest.phab.maniphest.search.side_effect = search
        return calls

    @staticmethod
    def _pages(calls):
        """The calls that read results, as against the one-row probes."""
        return [call for call in calls if "limit" not in call]

    @staticmethod
    def _probes(calls):
        """The one-row calls the bisection made to find the culprit."""
        return [call for call in calls if call.get("limit") == 1]

    def test_the_message_names_the_search_key_not_the_constraint(self, mock_init):
        maniphest = _maniphest()
        self._refuse(maniphest, "closerPHIDs")

        with pytest.raises(PhabfiveDataException) as error:
            maniphest.task_search(closed_by="alice")

        message = str(error.value)
        assert "closerPHIDs" in message
        assert "closed-by" in message
        assert "https://phab.example.com/api/" in message

    def test_only_the_refused_constraint_is_named(self, mock_init):
        """The other constraints of the same search are accepted, and unnamed.

        Conduit says only that some key is invalid, so naming every key the
        search sent would tell a person to drop three filters this instance
        answers perfectly well.
        """
        maniphest = _maniphest()
        self._refuse(maniphest, "closerPHIDs")

        with pytest.raises(PhabfiveDataException) as error:
            maniphest.task_search(closed_by="alice", subtype="bug", space="S1")

        message = str(error.value)
        assert "closed-by" in message
        assert "subtypes" not in message
        assert "spaces" not in message
        assert "statuses" not in message

    def test_a_refusal_that_cannot_be_isolated_names_candidates(self, mock_init):
        """No subset reproduces it, so nothing is named as the culprit."""
        maniphest = _maniphest()

        calls = []

        def search(**kwargs):
            calls.append(kwargs)
            if "limit" in kwargs:
                # Every probe is answered, so no single key is the culprit
                return _page([])
            raise PhabfiveAPIException(
                "ERR-INVALID-CONSTRAINT",
                'Parameter "constraints" includes an invalid key.',
            )

        maniphest.phab.maniphest.search.side_effect = search

        with pytest.raises(PhabfiveDataException) as error:
            maniphest.task_search(subtype="bug")

        message = str(error.value)
        assert "did not say which" in message
        assert "'subtypes' (from 'subtype')" in message

    def test_and_is_a_data_exception_the_command_already_catches(self, mock_init):
        # `maniphest search` answers PhabfiveConfigException and
        # PhabfiveDataException with "ERROR: ..." and exit 1; a
        # PhabfiveAPIException is neither, and would print a Conduit code.
        maniphest = _maniphest()
        self._refuse(maniphest, "subtypes")

        with pytest.raises(PhabfiveDataException):
            maniphest.task_search(subtype="bug")

    def test_an_optimisation_is_dropped_and_asked_again(self, mock_init):
        """A lift must never turn a search that worked into an error.

        `priorities` is only sent to save fetching tasks that cannot match,
        so an instance without it gets the same search without it - and the
        client-side filter still answers the question.
        """
        maniphest = _maniphest()
        calls = self._refuse(
            maniphest, "priorities", then=[_task(1, priority=("High", 80))]
        )

        kept = _run(maniphest, priority_patterns=parse_priority_patterns("in:High"))

        assert kept == [1]

        pages = self._pages(calls)
        assert len(pages) == 2
        assert "priorities" in pages[0]["constraints"]
        assert "priorities" not in pages[1]["constraints"]

        # And it asked which key it was rather than assuming the one it
        # happened to be narrowing with
        assert self._probes(calls)

    def test_the_warning_names_only_the_constraint_that_was_dropped(
        self, mock_init, caplog
    ):
        """Not every optional constraint the search happened to send."""
        maniphest = _maniphest([_task(1, priority=("High", 80), column=BACKLOG)])
        self._refuse(maniphest, "priorities")

        with caplog.at_level("WARNING"):
            _run(
                maniphest,
                tag="Board",
                column_patterns=parse_column_patterns("in:Backlog"),
                priority_patterns=parse_priority_patterns("in:High"),
            )

        warnings = [record.message for record in caplog.records]
        assert any("does not accept priorities" in one for one in warnings)
        assert not any("columnPHIDs" in one for one in warnings)

    def test_a_constraint_that_decides_results_is_never_dropped(self, mock_init):
        """Only a lift may be retried without.

        Dropping `subscribers` would answer a different question with a
        straight face, so it is an error instead.
        """
        maniphest = _maniphest()
        calls = self._refuse(maniphest, "subscribers")

        with pytest.raises(PhabfiveDataException):
            maniphest.task_search(subscriber="alice")

        # One attempt to read results, and no second one: the probes that
        # found the culprit are not a retry of the search.
        assert len(self._pages(calls)) == 1

    def test_another_api_error_is_left_alone(self, mock_init):
        maniphest = _maniphest()

        def search(**kwargs):
            raise PhabfiveAPIException("ERR-INVALID-AUTH", "API token invalid")

        maniphest.phab.maniphest.search.side_effect = search

        with pytest.raises(PhabfiveAPIException):
            maniphest.task_search(subtype="bug")


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestThePagingIsTheSharedOne:
    """Maniphest was the only app with a cursor loop of its own."""

    def test_the_cursor_loop_is_phabfive_pagination(self, mock_init):
        import phabfive.maniphest.core as core

        assert core.iter_pages is phabfive.pagination.iter_pages
        assert core.search_all_pages is phabfive.pagination.search_all_pages

    def test_every_page_is_read(self, mock_init):
        maniphest = _maniphest(pages=[[_task(1), _task(2)], [_task(3)]])

        assert sorted(_run(maniphest, subtype="bug")) == [1, 2, 3]

    def test_the_cursor_is_followed_by_the_shared_loop(self, mock_init):
        maniphest = _maniphest(pages=[[_task(1)], [_task(2)]])

        with patch(
            "phabfive.maniphest.core.iter_pages",
            side_effect=phabfive.pagination.iter_pages,
        ) as paged:
            _run(maniphest, subtype="bug")

        assert paged.called

    def test_no_total_limit_is_forwarded(self, mock_init):
        """--limit is applied after the client-side filters, not by paging.

        `iter_pages` would trim and stop early, which would return a
        different set of tasks: the limit keeps the top N of what matched,
        and what matched is not known until every page has been read.
        """
        maniphest = _maniphest(pages=[[_task(n) for n in range(1, 4)]])

        _run(maniphest, subtype="bug", limit=2)

        assert "limit" not in maniphest.phab.maniphest.search.call_args_list[0][1]

    def test_include_reads_more_than_one_page(self, mock_init):
        """--include fetched one unpaged search: over 100 ids were dropped.

        The ids constraint takes as many as it is given, but one response
        still holds at most a hundred tasks and a cursor.
        """
        maniphest = _maniphest()
        included = [_task(n) for n in range(1, 151)]

        def search(**kwargs):
            after = kwargs.get("after")
            if after is None:
                return _page(included[:100], after="1")
            return _page(included[100:])

        maniphest.phab.maniphest.search.side_effect = search

        kept = _run(maniphest, include_task_ids=[t["id"] for t in included])

        assert len(kept) == 150


class TestTheCommandAndTheTemplateReachThem:
    """A constraint nothing can ask for is a constraint that does not exist.

    The registry declares each key once; these are the two ways a person
    supplies one - a flag, and a `search:` key in a spec - checked against
    the keywords `task_search` actually receives, so that a key cannot be
    declared, accepted by the loader and then quietly dropped (#295).
    """

    def _search(self, argv, configs=None):
        """Run `maniphest search` with a mocked app, answering its kwargs."""
        app = MagicMock()
        app.task_search.return_value = {"tasks": []}
        app.parse_status_patterns_with_api.side_effect = lambda value: None

        if configs is not None:
            app._load_search_config.return_value = list(configs)

        patches = [
            patch("phabfive.cli.maniphest._get_maniphest_app", return_value=app),
            patch("phabfive.cli.maniphest._display_tasks"),
        ]

        with contextlib.ExitStack() as stack:
            for one in patches:
                stack.enter_context(one)
            result = CliRunner().invoke(maniphest_app, argv)

        assert result.exit_code == 0, result.output

        return app.task_search.call_args[1]

    @pytest.mark.parametrize(
        "argv, keyword, value",
        [
            (["--ids", "T1,T2"], "ids", "T1,T2"),
            (["--phids", "PHID-TASK-a"], "phids", "PHID-TASK-a"),
            (["--subscriber", "alice"], "subscriber", "alice"),
            (["--subtype", "bug"], "subtype", "bug"),
            (["--parent", "T7"], "parent", "T7"),
            (["--subtask", "T9"], "subtask", "T9"),
            (["--has-parents"], "has_parents", True),
            (["--has-subtasks"], "has_subtasks", True),
            (["--closed-by", "alice"], "closed_by", "alice"),
            (["--closed-after", "7d"], "closed_after", "7d"),
            (["--closed-before", "7d"], "closed_before", "7d"),
        ],
    )
    def test_the_flag_reaches_task_search(self, argv, keyword, value):
        assert self._search(["search", *argv])[keyword] == value

    @pytest.mark.parametrize(
        "key, keyword, value",
        [
            ("ids", "ids", "T1,T2"),
            ("phids", "phids", "PHID-TASK-a"),
            ("subscriber", "subscriber", "alice"),
            ("subtype", "subtype", "bug"),
            ("parent", "parent", "T7"),
            ("subtask", "subtask", "T9"),
            ("has-parents", "has_parents", True),
            ("has-subtasks", "has_subtasks", True),
            ("closed-by", "closed_by", "alice"),
            ("closed-after", "closed_after", "7d"),
            ("closed-before", "closed_before", "7d"),
        ],
    )
    def test_the_template_key_reaches_task_search(self, key, keyword, value):
        configs = [{"search": {key: value}, "title": None, "description": None}]

        assert self._search(["search", "--with", "t.yaml"], configs)[keyword] == value

    def test_a_flag_still_beats_the_template(self):
        configs = [{"search": {"subtype": "chore"}, "title": None, "description": None}]

        kwargs = self._search(
            ["search", "--with", "t.yaml", "--subtype", "bug"], configs
        )

        assert kwargs["subtype"] == "bug"

    def test_a_false_boolean_alone_is_a_search(self):
        """`has-parents: false` is the tasks with no parent, which is a filter.

        The criteria guard is a truthiness test over the planned parameters,
        so this used to print the command's usage and exit 2 for a search
        `task_search` runs perfectly well. `SearchPlan.has_criteria` tests
        the tri-state keys for presence instead.
        """
        app = MagicMock()
        app.task_search.return_value = {"tasks": []}
        app.parse_status_patterns_with_api.side_effect = lambda value: None
        app._load_search_config.return_value = [
            {
                "type": None,
                "search": {"has-parents": False},
                "title": None,
                "description": None,
            }
        ]

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=app):
            result = CliRunner().invoke(maniphest_app, ["search", "--with", "t.yaml"])

        assert result.exit_code == 0
        assert app.task_search.call_args.kwargs["has_parents"] is False

    def test_a_false_boolean_beside_another_key_is_searched_for(self):
        kwargs = self._search(
            ["search", "--with", "t.yaml"],
            [
                {
                    "search": {"has-parents": False, "subtype": "bug"},
                    "title": None,
                    "description": None,
                }
            ],
        )

        assert kwargs["has_parents"] is False

    def test_a_flag_nobody_typed_does_not_clobber_the_template(self):
        """The whole point of the command's sentinels, for the new keys too."""
        configs = [
            {"search": {"has-parents": True}, "title": None, "description": None}
        ]

        kwargs = self._search(["search", "--with", "t.yaml"], configs)

        assert kwargs["has_parents"] is True
