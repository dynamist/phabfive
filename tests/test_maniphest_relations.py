# -*- coding: utf-8 -*-

"""--show-relations: Parents, Subtasks and Commits for many tasks at once (#542)."""

import logging
from unittest.mock import MagicMock, patch

from phabfive.exceptions import PhabfiveAPIException
from phabfive.maniphest import Maniphest
from phabfive.maniphest.fetchers import fetch_edges_for_tasks, search_edges

URL = "https://phorge.example.com"

#: T1 is the parent of T2 and T3; T2 has T9 (outside every result) as a
#: second parent and a commit attached.
EDGES = [
    ("PHID-TASK-1", "task.subtask", "PHID-TASK-2"),
    ("PHID-TASK-1", "task.subtask", "PHID-TASK-3"),
    ("PHID-TASK-2", "task.parent", "PHID-TASK-1"),
    ("PHID-TASK-2", "task.parent", "PHID-TASK-9"),
    ("PHID-TASK-2", "task.commit", "PHID-CMIT-1"),
    ("PHID-TASK-3", "task.parent", "PHID-TASK-1"),
]


def _task(task_id):
    return {
        "id": task_id,
        "phid": f"PHID-TASK-{task_id}",
        "fields": {
            "name": f"Task {task_id}",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "Normal", "value": 50},
            "description": {"raw": ""},
        },
        "attachments": {"columns": {"boards": {}}},
    }


def _edge_search(**kwargs):
    """edge.search over EDGES, one edge per page to exercise the cursor."""
    matching = [
        {"sourcePHID": source, "edgeType": edge_type, "destinationPHID": destination}
        for source, edge_type, destination in EDGES
        if source in kwargs["sourcePHIDs"] and edge_type in kwargs["types"]
    ]
    start = int(kwargs.get("after") or 0)
    after = str(start + 1) if start + 1 < len(matching) else None
    return {"data": matching[start : start + 1], "cursor": {"after": after}}


def _maniphest(matched):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = URL
    maniphest.conf = {"PHAB_SPACE": "S1"}
    maniphest.phab.project.query.return_value = {"data": {}}
    maniphest.phab.edge.search.side_effect = _edge_search
    maniphest.phab.phid.query.return_value = {
        "PHID-CMIT-1": {
            "type": "CMIT",
            "name": "rX0123456789ab",
            "fullName": "rX0123456789ab: fix it",
            "uri": f"{URL}/rX0123456789ab",
        }
    }

    def search(**kwargs):
        constraints = kwargs.get("constraints", {})
        if "phids" in constraints:
            data = [_task(int(p.rsplit("-", 1)[1])) for p in constraints["phids"]]
        elif "ids" in constraints:
            data = [_task(i) for i in constraints["ids"]]
        else:
            data = matched
        response = MagicMock()
        response.response = {"data": data}
        return response

    maniphest.phab.maniphest.search.side_effect = search
    return maniphest


def _link(task_id):
    return {"Link": f"{URL}/T{task_id}", "Task": {"Name": f"Task {task_id}"}}


def _by_id(result):
    return {int(t["_url"].rsplit("/T", 1)[1]): t for t in result["tasks"]}


class TestFetchEdgesForTasks:
    def test_every_page_is_read_and_split_by_task_and_relationship(self):
        phab = MagicMock()
        phab.edge.search.side_effect = _edge_search

        edges = fetch_edges_for_tasks(
            phab, ["PHID-TASK-1", "PHID-TASK-2"], ["parents", "subtasks"]
        )

        assert edges == {
            "PHID-TASK-1": {
                "parents": [],
                "subtasks": ["PHID-TASK-2", "PHID-TASK-3"],
            },
            "PHID-TASK-2": {
                "parents": ["PHID-TASK-1", "PHID-TASK-9"],
                "subtasks": [],
            },
        }
        assert phab.edge.search.call_args.kwargs["types"] == [
            "task.parent",
            "task.subtask",
        ]

    def test_no_tasks_is_no_call(self):
        phab = MagicMock()

        assert fetch_edges_for_tasks(phab, [], ["parents"]) == {}
        phab.edge.search.assert_not_called()


class _EdgeServer:
    """edge.search as Phorge pages it (#547).

    Rows are ordered newest first by ``(dateCreated, seq)``, a page is cut
    after `limit` rows (at most 1000), its cursor is ``<dateCreated>_<seq>``
    of the last row, and the next page holds the rows strictly below that
    pair. `seq` restarts in every edit, so rows created in the same second
    tie, and the server skips the tied rows that did not fit on a page.
    """

    def __init__(self, rows):
        #: (source, edgeType, destination, dateCreated, seq)
        self.rows = rows
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        rows = sorted(
            (
                row
                for row in self.rows
                if row[0] in kwargs["sourcePHIDs"] and row[1] in kwargs["types"]
            ),
            key=lambda row: (row[3], row[4]),
            reverse=True,
        )
        if kwargs.get("after"):
            created, seq = (int(part) for part in kwargs["after"].split("_"))
            rows = [row for row in rows if (row[3], row[4]) < (created, seq)]
        limit = min(1000, kwargs.get("limit", 100))
        page = rows[:limit]
        more = len(rows) > limit
        return {
            "data": [
                {"sourcePHID": src, "edgeType": edge_type, "destinationPHID": dst}
                for src, edge_type, dst, _, _ in page
            ],
            "cursor": {"after": f"{page[-1][3]}_{page[-1][4]}" if more else None},
        }


def _children(count, created=1700000000):
    """`count` subtasks set on one parent in one edit: each child's parent
    edge is created in the same second with seq 0, so every one ties."""
    children = [f"PHID-TASK-C{i}" for i in range(count)]
    rows = [(child, "task.parent", "PHID-TASK-P", created, 0) for child in children]
    rows += [
        ("PHID-TASK-P", "task.subtask", child, created, seq)
        for seq, child in enumerate(children)
    ]
    return children, rows


class TestEdgeSearchTies:
    """edge.search's cursor skips edges created in the same second (#547)."""

    def test_tied_edges_past_the_first_page_are_all_read(self):
        children, rows = _children(150)
        phab = MagicMock()
        phab.edge.search.side_effect = _EdgeServer(rows)

        edges = fetch_edges_for_tasks(
            phab, children + ["PHID-TASK-P"], ["parents", "subtasks"]
        )

        assert all(edges[child]["parents"] == ["PHID-TASK-P"] for child in children)
        assert sorted(edges["PHID-TASK-P"]["subtasks"]) == sorted(children)

    def test_a_tie_across_a_page_boundary_is_read_again_not_skipped(self):
        """90 newer edges, then 30 tied older ones that straddle the first page."""
        rows = [
            (f"PHID-TASK-{i}", "task.parent", "PHID-TASK-NEW", 1700000000 + i, 0)
            for i in range(90)
        ]
        rows += [
            (f"PHID-TASK-T{i}", "task.parent", "PHID-TASK-OLD", 1600000000, 0)
            for i in range(30)
        ]
        server = _EdgeServer(rows)

        records = search_edges(
            server,
            sourcePHIDs=[row[0] for row in rows],
            types=["task.parent"],
        )

        assert len(records) == 120
        assert len({record["sourcePHID"] for record in records}) == 120
        assert [call.get("after") for call in server.calls] == [
            None,
            "1600000000_1",
        ]

    def test_a_page_of_one_tie_is_asked_for_again_at_the_largest_size(self):
        children, rows = _children(250)
        server = _EdgeServer(rows)

        records = search_edges(server, sourcePHIDs=children, types=["task.parent"])

        assert len(records) == 250
        assert [call["limit"] for call in server.calls] == [100, 100, 1000]

    def test_a_tie_larger_than_any_page_is_paged_past_with_a_warning(self, caplog):
        children, rows = _children(1200)
        rows.append(("PHID-TASK-C0", "task.parent", "PHID-TASK-OLD", 1600000000, 0))
        server = _EdgeServer(rows)

        with caplog.at_level(logging.WARNING, logger="phabfive.maniphest.fetchers"):
            records = search_edges(server, sourcePHIDs=children, types=["task.parent"])

        assert len(records) == 1001
        assert "may be missing" in caplog.text

    def test_a_cursor_of_another_form_is_followed_as_it_is(self):
        pages = {
            None: {"data": [{"sourcePHID": "A"}], "cursor": {"after": "abc"}},
            "abc": {"data": [{"sourcePHID": "B"}], "cursor": {"after": None}},
        }
        calls = []

        def search(**kwargs):
            calls.append(kwargs.get("after"))
            return pages[kwargs.get("after")]

        assert [r["sourcePHID"] for r in search_edges(search)] == ["A", "B"]
        assert calls == [None, "abc"]


class TestSearchShowRelations:
    def test_relations_inside_and_outside_the_result_are_published(self):
        maniphest = _maniphest([_task(1), _task(2)])

        result = maniphest.task_search(text_query="x", show_relations=True)

        tasks = _by_id(result)
        assert tasks[1]["Parents"] == []
        assert tasks[1]["Subtasks"] == [_link(2), _link(3)]
        assert tasks[2]["Parents"] == [_link(1), _link(9)]
        assert tasks[2]["Commits"] == [
            {
                "Link": f"{URL}/rX0123456789ab",
                "Commit": {"Identifier": "rX0123456789ab", "Summary": "fix it"},
            }
        ]

    def test_only_the_tasks_outside_the_result_are_looked_up(self):
        maniphest = _maniphest([_task(1), _task(2)])

        maniphest.task_search(text_query="x", show_relations=True)

        lookups = [
            call.kwargs["constraints"]["phids"]
            for call in maniphest.phab.maniphest.search.call_args_list
            if "phids" in call.kwargs.get("constraints", {})
        ]
        assert lookups == [["PHID-TASK-3", "PHID-TASK-9"]]
        # One edge.search for the whole result, however many pages it has
        sources = {
            frozenset(call.kwargs["sourcePHIDs"])
            for call in maniphest.phab.edge.search.call_args_list
        }
        assert sources == {frozenset({"PHID-TASK-1", "PHID-TASK-2"})}

    def test_without_the_flag_no_relation_is_asked_for(self):
        maniphest = _maniphest([_task(1), _task(2)])

        result = maniphest.task_search(text_query="x")

        maniphest.phab.edge.search.assert_not_called()
        maniphest.phab.phid.query.assert_not_called()
        # Not an empty list, which would say the tasks have no parents
        for task in _by_id(result).values():
            assert not {"Parents", "Subtasks", "Commits"} & task.keys()

    def test_a_task_with_none_says_so(self):
        maniphest = _maniphest([_task(3)])

        result = maniphest.task_search(text_query="x", show_relations=True)

        assert _by_id(result)[3]["Subtasks"] == []
        assert _by_id(result)[3]["Commits"] == []

    def test_a_failed_lookup_still_answers_with_the_tasks(self):
        """Without the keys: an empty list would claim the task has none."""
        maniphest = _maniphest([_task(1)])
        maniphest.phab.edge.search.side_effect = PhabfiveAPIException("ERR-X", "boom")

        result = maniphest.task_search(text_query="x", show_relations=True)

        assert "Subtasks" not in _by_id(result)[1]


class TestShowShowRelations:
    def test_the_same_records_as_search(self):
        maniphest = _maniphest([])

        result = maniphest.task_show([2, 1], show_relations=True)

        tasks = _by_id(result)
        assert tasks[2]["Parents"] == [_link(1), _link(9)]
        assert tasks[1]["Subtasks"] == [_link(2), _link(3)]
        assert maniphest.phab.phid.query.call_count == 1

    def test_without_the_flag_no_relation_is_asked_for(self):
        maniphest = _maniphest([])

        maniphest.task_show([1])

        maniphest.phab.edge.search.assert_not_called()


class TestRelatedTasks:
    def test_the_related_tasks_carry_their_own_relations(self):
        maniphest = _maniphest([])

        result = maniphest.get_related_tasks(1, "subtasks")

        tasks = _by_id(result)
        assert sorted(tasks) == [2, 3]
        assert tasks[2]["Parents"] == [_link(1), _link(9)]
        # Commits are not part of what `parents`/`subtasks` list
        assert "Commits" not in tasks[2]
