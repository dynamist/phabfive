# -*- coding: utf-8 -*-

"""Tests for deterministic result ordering and the --order option.

`maniphest search` used to return multi-project results in an order that
changed from run to run: the project PHIDs came out of a set, and string
hashing is randomised per process, so the per-project fetch loop visited them
in a different order each time. Since `--limit` truncates that merge, the same
command could return a different *set* of tasks on the next run.

The ordering is applied to the merged result before the limit, so
`--limit N --order X` means "the top N by X".
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.constants import (
    MANIPHEST_ORDER_CHOICES,
    MANIPHEST_ORDER_DEFAULT,
    MANIPHEST_ORDER_DIRECTIONS,
    MANIPHEST_ORDER_FIELDS,
)
from phabfive.exceptions import PhabfiveConfigException
from phabfive.cli.maniphest import maniphest_app
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.utils import (
    MANIPHEST_SORT_KEYS,
    PHORGE_ORDER_KEYS,
    sort_tasks,
)
from phabfive.ordering import parse_order

runner = CliRunner()

PROJECT_A = "PHID-PROJ-aaaaaaaaaaaaaaaaaaaa"
PROJECT_B = "PHID-PROJ-bbbbbbbbbbbbbbbbbbbb"


def _order(value):
    return parse_order(
        value,
        MANIPHEST_ORDER_FIELDS,
        MANIPHEST_ORDER_DIRECTIONS,
        MANIPHEST_ORDER_DEFAULT,
    )


def _task(task_id, priority=50, modified=0, closed=None, name=None):
    return {
        "id": task_id,
        "phid": f"PHID-TASK-{task_id}",
        "fields": {
            "name": name if name is not None else f"Task {task_id}",
            "status": {"name": "Open"},
            "priority": {"name": "Normal", "value": priority},
            "description": {"raw": ""},
            "dateCreated": task_id,
            "dateModified": modified,
            "dateClosed": closed,
        },
        "attachments": {"columns": {"boards": {}}},
    }


class TestParseOrder:
    def test_none_falls_back_to_the_default(self):
        assert _order(None) == ("priority", "desc")
        assert _order("") == ("priority", "desc")
        assert _order("   ") == ("priority", "desc")

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("priority", ("priority", "desc")),
            ("updated", ("updated", "desc")),
            ("created", ("created", "desc")),
            ("closed", ("closed", "desc")),
            ("title", ("title", "asc")),
            ("relevance", ("relevance", None)),
        ],
    )
    def test_bare_field_means_the_useful_direction(self, value, expected):
        assert _order(value) == expected

    @pytest.mark.parametrize("field", ["priority", "updated", "created", "closed"])
    @pytest.mark.parametrize("direction", ["asc", "desc"])
    def test_explicit_direction_on_every_directional_field(self, field, direction):
        assert _order(f"{field}:{direction}") == (field, direction)

    def test_title_takes_both_directions_too(self):
        assert _order("title:asc") == ("title", "asc")
        assert _order("title:desc") == ("title", "desc")

    def test_case_and_whitespace_are_normalised(self):
        assert _order("PRIORITY") == ("priority", "desc")
        assert _order("  Updated:ASC  ") == ("updated", "asc")

    def test_unknown_field_lists_the_valid_ones(self):
        with pytest.raises(PhabfiveConfigException) as excinfo:
            _order("bogus")

        message = str(excinfo.value)
        assert "Invalid order 'bogus'" in message
        for field in MANIPHEST_ORDER_FIELDS:
            assert field in message

    def test_unknown_direction_is_rejected(self):
        with pytest.raises(PhabfiveConfigException):
            _order("title:sideways")

    def test_relevance_takes_no_direction(self):
        with pytest.raises(PhabfiveConfigException) as excinfo:
            _order("relevance:asc")

        assert "takes no direction" in str(excinfo.value)

    @pytest.mark.parametrize(
        "phorge_name,suggestion",
        [
            ("newest", "created"),
            ("oldest", "created:asc"),
            ("outdated", "updated:asc"),
            ("name", "title"),
        ],
    )
    def test_phorge_own_names_point_at_the_phabfive_spelling(
        self, phorge_name, suggestion
    ):
        with pytest.raises(PhabfiveConfigException) as excinfo:
            _order(phorge_name)

        assert f"Did you mean '{suggestion}'?" in str(excinfo.value)


class TestSortKeys:
    # Two tasks share a priority, two share a dateModified and two share a
    # title, so every tie-break is exercised.
    TASKS = [
        _task(1, priority=50, modified=300, closed=None, name="Banana"),
        _task(2, priority=80, modified=100, closed=1000, name="apple"),
        _task(3, priority=50, modified=200, closed=None, name="Cherry"),
        _task(4, priority=80, modified=100, closed=500, name="apple"),
    ]

    @pytest.mark.parametrize(
        "order,expected",
        [
            ("priority", [4, 2, 3, 1]),
            ("priority:asc", [1, 3, 2, 4]),
            ("updated", [1, 3, 4, 2]),
            ("updated:asc", [2, 4, 3, 1]),
            ("created", [4, 3, 2, 1]),
            ("created:asc", [1, 2, 3, 4]),
            ("closed", [2, 4, 3, 1]),
            ("closed:asc", [4, 2, 1, 3]),
            ("title", [4, 2, 1, 3]),
            ("title:desc", [3, 1, 2, 4]),
        ],
    )
    def test_each_order(self, order, expected):
        field, direction = _order(order)

        assert [t["id"] for t in sort_tasks(self.TASKS, field, direction)] == expected

    @pytest.mark.parametrize("order", ["closed", "closed:asc"])
    def test_open_tasks_sort_last_in_both_directions(self, order):
        field, direction = _order(order)

        ids = [t["id"] for t in sort_tasks(self.TASKS, field, direction)]

        assert set(ids[-2:]) == {1, 3}, "tasks with no dateClosed belong at the tail"

    def test_title_is_case_insensitive(self):
        field, direction = _order("title")

        ids = [t["id"] for t in sort_tasks(self.TASKS, field, direction)]

        # "apple" before "Banana" before "Cherry", not ASCII order
        assert ids == [4, 2, 1, 3]

    @pytest.mark.parametrize(
        "order", [c for c in MANIPHEST_ORDER_CHOICES if c != "relevance"]
    )
    def test_every_order_is_a_total_order(self, order):
        """Tasks alike in every sorted field still come back in a fixed order."""
        field, direction = _order(order)
        twins = [
            _task(7, priority=50, modified=5, closed=9, name="same"),
            _task(8, priority=50, modified=5, closed=9, name="same"),
        ]

        first = [t["id"] for t in sort_tasks(twins, field, direction)]
        second = [t["id"] for t in sort_tasks(list(reversed(twins)), field, direction)]

        assert first == second

    def test_input_list_is_not_mutated(self):
        field, direction = _order("created:asc")
        tasks = [_task(2), _task(1)]

        sort_tasks(tasks, field, direction)

        assert [t["id"] for t in tasks] == [2, 1]


class TestSortToleratesSparseTasks:
    """Partial API responses and older fixtures must not blow up the sort."""

    SPARSE = [
        {"id": 9, "fields": {"priority": {"name": "Normal"}}},
        {"id": 4, "fields": {}},
        {"id": 7},
    ]

    @pytest.mark.parametrize("order", MANIPHEST_ORDER_CHOICES)
    def test_sorts_without_raising(self, order):
        field, direction = _order(order)

        assert len(sort_tasks(self.SPARSE, field, direction)) == 3


class TestRelevance:
    def test_server_order_is_preserved(self):
        field, direction = _order("relevance")
        tasks = [_task(1), _task(3), _task(2)]

        assert [t["id"] for t in sort_tasks(tasks, field, direction)] == [1, 3, 2]

    def test_has_no_client_side_key(self):
        assert ("relevance", None) not in MANIPHEST_SORT_KEYS
        assert PHORGE_ORDER_KEYS[("relevance", None)] == "relevance"


class TestOrderTables:
    def test_every_choice_maps_to_an_api_order(self):
        for choice in MANIPHEST_ORDER_CHOICES:
            assert _order(choice) in PHORGE_ORDER_KEYS

    def test_every_choice_but_relevance_has_a_sort_key(self):
        for choice in MANIPHEST_ORDER_CHOICES:
            field, direction = _order(choice)
            if field == "relevance":
                continue
            assert (field, direction) in MANIPHEST_SORT_KEYS


def _maniphest(project_phids=None, tasks_by_project=None, tasks=None):
    """A Maniphest whose searches return canned tasks."""
    maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "https://phabricator.example.com"
    maniphest.conf = {"PHAB_SPACE": "S1"}
    maniphest.phab.phid.lookup.return_value = {
        "S1": {
            "phid": "PHID-SPCE-1",
            "name": "Global",
            "fullName": "Global",
            "uri": "/S1",
        }
    }
    maniphest.phab.project.query.return_value = {"data": {}}
    maniphest._resolve_project_phids = MagicMock(return_value=project_phids or [])
    maniphest._get_open_statuses = MagicMock(return_value=["open"])

    def side_effect(**kwargs):
        constraints = kwargs.get("constraints", {})
        resp = MagicMock()
        if "ids" in constraints:
            resp.response = {"data": []}
        elif tasks_by_project is not None:
            projects = constraints.get("projects") or []
            data = []
            for phid in projects:
                for task in tasks_by_project.get(phid, []):
                    task = dict(task)
                    # The --tag post-filter reads project membership off the
                    # board attachment, so a canned task needs one
                    task["attachments"] = {"columns": {"boards": {phid: {}}}}
                    data.append(task)
            resp.response = {"data": data}
        else:
            resp.response = {"data": list(tasks or [])}
        resp.get.return_value = {"after": None}
        return resp

    maniphest.phab.maniphest.search.side_effect = side_effect
    return maniphest


def _ids(result):
    return [int(t["_url"].rsplit("/T", 1)[1]) for t in result["tasks"]]


def _orders_sent(maniphest):
    return [
        call[1].get("order")
        for call in maniphest.phab.maniphest.search.call_args_list
        if "ids" not in call[1].get("constraints", {})
    ]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestOrderReachesApi:
    def test_default_order_on_the_untagged_path(self, _init):
        maniphest = _maniphest(tasks=[_task(1)])

        maniphest.task_search(text_query="x")

        assert _orders_sent(maniphest) == ["priority"]

    def test_default_order_on_the_single_project_path(self, _init):
        maniphest = _maniphest(project_phids=[PROJECT_A], tasks=[_task(1)])

        maniphest.task_search(tag="A")

        assert _orders_sent(maniphest) == ["priority"]

    def test_default_order_on_the_multi_project_path(self, _init):
        maniphest = _maniphest(
            project_phids=[PROJECT_A, PROJECT_B],
            tasks_by_project={PROJECT_A: [_task(1)], PROJECT_B: [_task(2)]},
        )

        maniphest.task_search(tag="A,B")

        assert _orders_sent(maniphest) == ["priority", "priority"]

    @pytest.mark.parametrize(
        "order,api_order",
        [
            ("updated", "updated"),
            ("updated:asc", "outdated"),
            ("created", "newest"),
            ("created:asc", "oldest"),
            ("title", "title"),
            ("relevance", "relevance"),
            # No Phorge builtin for these, so the closest one is sent and the
            # client-side sort flips it
            ("priority:asc", "priority"),
            ("closed:asc", "closed"),
            ("title:desc", "title"),
        ],
    )
    def test_phabfive_orders_map_onto_phorge_builtins(self, _init, order, api_order):
        maniphest = _maniphest(tasks=[_task(1)])

        maniphest.task_search(text_query="x", order=order)

        assert _orders_sent(maniphest) == [api_order]

    def test_include_fetch_is_unaffected(self, _init):
        maniphest = _maniphest(tasks=[_task(1)])

        maniphest.task_search(text_query="x", include_task_ids=[2069])

        id_calls = [
            call[1]
            for call in maniphest.phab.maniphest.search.call_args_list
            if "ids" in call[1].get("constraints", {})
        ]
        assert id_calls and all("order" not in kwargs for kwargs in id_calls)

    def test_invalid_order_fails_before_any_request(self, _init):
        maniphest = _maniphest(tasks=[_task(1)])

        with pytest.raises(PhabfiveConfigException):
            maniphest.task_search(text_query="x", order="bogus")

        maniphest.phab.maniphest.search.assert_not_called()


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestDeterministicProjectIteration:
    """The regression test for the nondeterminism itself."""

    def _projects_queried(self, maniphest):
        return [
            call[1]["constraints"]["projects"][0]
            for call in maniphest.phab.maniphest.search.call_args_list
            if call[1].get("constraints", {}).get("projects")
        ]

    @pytest.mark.parametrize(
        "resolved", [[PROJECT_A, PROJECT_B], [PROJECT_B, PROJECT_A]]
    )
    def test_projects_are_visited_in_sorted_order(self, _init, resolved):
        maniphest = _maniphest(
            project_phids=resolved,
            tasks_by_project={PROJECT_A: [_task(1)], PROJECT_B: [_task(2)]},
        )

        maniphest.task_search(tag="A,B")

        assert self._projects_queried(maniphest) == [PROJECT_A, PROJECT_B]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestOrderingIsGlobal:
    def test_results_are_not_grouped_by_project(self, _init):
        """A high-priority task in the second project still comes first."""
        maniphest = _maniphest(
            project_phids=[PROJECT_A, PROJECT_B],
            tasks_by_project={
                PROJECT_A: [_task(1, priority=25)],
                PROJECT_B: [_task(2, priority=90)],
            },
        )

        result = maniphest.task_search(tag="A,B")

        assert _ids(result) == [2, 1]

    def test_order_applies_before_the_limit(self, _init):
        """--limit keeps the top N of the order, not an arbitrary slice."""
        maniphest = _maniphest(
            tasks=[
                _task(1, priority=25),
                _task(2, priority=90),
                _task(3, priority=80),
            ]
        )

        result = maniphest.task_search(text_query="x", limit=2)

        assert _ids(result) == [2, 3]

    def test_exclusion_still_frees_limit_slots(self, _init):
        maniphest = _maniphest(
            tasks=[
                _task(1, priority=25),
                _task(2, priority=90),
                _task(3, priority=80),
            ]
        )

        result = maniphest.task_search(text_query="x", exclude_task_ids=[2], limit=2)

        assert _ids(result) == [3, 1]

    def test_included_tasks_stay_at_the_tail(self, _init):
        """--include is a pin, so it outranks the ordering as well as the limit."""
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"
        maniphest.conf = {"PHAB_SPACE": "S1"}
        maniphest.phab.phid.lookup.return_value = {
            "S1": {
                "phid": "PHID-SPCE-1",
                "name": "Global",
                "fullName": "Global",
                "uri": "/S1",
            }
        }
        maniphest.phab.project.query.return_value = {"data": {}}
        maniphest._get_open_statuses = MagicMock(return_value=["open"])

        def side_effect(**kwargs):
            resp = MagicMock()
            if "ids" in kwargs.get("constraints", {}):
                resp.response = {"data": [_task(2069, priority=100)]}
            else:
                resp.response = {"data": [_task(1, priority=25), _task(2, priority=50)]}
            resp.get.return_value = {"after": None}
            return resp

        maniphest.phab.maniphest.search.side_effect = side_effect

        result = maniphest.task_search(text_query="x", include_task_ids=[2069])

        assert _ids(result) == [2, 1, 2069]


class TestOrderCli:
    def _invoke(self, args):
        mock_m = MagicMock()
        mock_m.task_search.return_value = None
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, ["search", *args])
        return result, mock_m

    def test_order_is_passed_through(self):
        result, mock_m = self._invoke(["--tag", "proj", "--order", "updated:asc"])

        assert result.exit_code == 0
        assert mock_m.task_search.call_args[1]["order"] == "updated:asc"

    def test_short_flag(self):
        result, mock_m = self._invoke(["--tag", "proj", "-o", "title"])

        assert result.exit_code == 0
        assert mock_m.task_search.call_args[1]["order"] == "title"

    def test_omitted_order_stays_none(self):
        """So a template's order: is not overridden by a CLI default."""
        result, mock_m = self._invoke(["--tag", "proj"])

        assert result.exit_code == 0
        assert mock_m.task_search.call_args[1]["order"] is None

    def test_invalid_order_is_a_clean_error(self):
        mock_m = MagicMock()
        mock_m.task_search.side_effect = PhabfiveConfigException(
            "Invalid order 'bogus'. Valid fields: priority, updated, created, "
            "closed, title, relevance."
        )
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(
                maniphest_app, ["search", "--tag", "p", "--order", "bogus"]
            )

        assert result.exit_code == 1
        assert "ERROR: Invalid order 'bogus'" in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)


class TestOrderInTemplates:
    def _run(self, template, args):
        mock_m = MagicMock()
        mock_m.task_search.return_value = None
        mock_m._load_search_config.return_value = [
            {"search": template, "title": "Command Line Search", "description": None}
        ]
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(
                maniphest_app, ["search", "--with", "template.yaml", *args]
            )
        return result, mock_m

    def test_template_order_is_honoured(self):
        result, mock_m = self._run({"tag": "proj", "order": "title"}, [])

        assert result.exit_code == 0
        assert mock_m.task_search.call_args[1]["order"] == "title"

    def test_cli_order_overrides_the_template(self):
        result, mock_m = self._run(
            {"tag": "proj", "order": "title"}, ["--order", "created:asc"]
        )

        assert result.exit_code == 0
        assert mock_m.task_search.call_args[1]["order"] == "created:asc"

    def test_order_is_an_accepted_template_parameter(self, tmp_path):
        """A YAML template with order: loads instead of being rejected."""
        template = tmp_path / "search.yaml"
        template.write_text("search:\n  tag: proj\n  order: title:desc\n")
        maniphest = Maniphest.__new__(Maniphest)

        configs = maniphest._load_search_config(str(template))

        assert configs[0]["search"]["order"] == "title:desc"
