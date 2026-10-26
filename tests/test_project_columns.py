# -*- coding: utf-8 -*-

"""Workboard columns: `project show --columns`, and `--column` on edit (#588)."""

# python std lib
import json

# 3rd party imports
from unittest import mock

import pytest

# phabfive imports
from phabfive.exceptions import (
    PhabfiveDataException,
    PhabfiveInputException,
    PhabfiveNotFoundException,
)
from phabfive.maniphest.fetchers import get_column_info
from phabfive.project.fetchers import (
    board_order,
    fetch_board_columns,
    fetch_boards_columns,
)
from phabfive.project.formatters import format_column
from tests.test_project import (
    ADMIN,
    ALL,
    DEPLOYBOT,
    FakePhab,
    _app,
    _invoke,
)


def _column(
    cid,
    name,
    sequence,
    board="PHID-PROJ-12",
    hidden=False,
    default=False,
    proxy=None,
):
    """A project.column.search result item."""
    return {
        "id": cid,
        "phid": f"PHID-PCOL-{cid}",
        "fields": {
            "name": name,
            "proxyPHID": proxy,
            "project": {"phid": board},
            "isHidden": hidden,
            "isDefaultColumn": default,
            "sequence": sequence,
        },
    }


#: The seeded Development board: five columns, a hidden one after them and
#: the column of its milestone Sprint 1 (PHID-PROJ-13), newest first, the way
#: project.column.search answers.
DEVELOPMENT_BOARD = [
    _column(47, "Sprint 1", 6, proxy="PHID-PROJ-13"),
    _column(46, "Won't Do", 5, hidden=True),
    _column(20, "Done", 4),
    _column(19, "In Review", 3),
    _column(18, "In Progress", 2),
    _column(17, "Up Next", 1),
    _column(16, "Backlog", 0, default=True),
]

BOARD_ORDER = ["Backlog", "Up Next", "In Progress", "In Review", "Done"]


class ColumnPhab(FakePhab):
    """FakePhab with workboards: board PHID to its columns."""

    def __init__(self, boards, **kwargs):
        super().__init__(ALL, users=[ADMIN, DEPLOYBOT], **kwargs)
        self.boards = boards
        self.project.column.search.side_effect = self._column_search

    def _column_search(self, constraints=None, **_):
        found = [
            {
                **column,
                "fields": {**column["fields"], "project": {"phid": board}},
            }
            for board in (constraints or {}).get("projects", [])
            for column in self.boards.get(board, [])
        ]
        return {"data": found, "cursor": {"after": None}}


@pytest.fixture
def phab():
    return ColumnPhab({"PHID-PROJ-12": DEVELOPMENT_BOARD})


@pytest.fixture
def restore_output_format():
    from phabfive.core import Phabfive

    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


def _names(columns):
    return [column["fields"]["name"] for column in columns]


class TestBoardOrder:
    def test_by_sequence_not_api_order(self):
        assert _names(board_order(DEVELOPMENT_BOARD[2:])) == BOARD_ORDER

    def test_milestones_go_last_by_milestone_number(self):
        """A column added after the milestones still comes before them."""
        columns = [
            _column(3, "Sprint 2", 1, proxy="PHID-PROJ-m2"),
            _column(2, "Sprint 1", 2, proxy="PHID-PROJ-m1"),
            _column(4, "Added later", 3),
            _column(1, "Backlog", 0),
        ]
        proxies = {
            "PHID-PROJ-m1": {"fields": {"milestone": 1}},
            "PHID-PROJ-m2": {"fields": {"milestone": 2}},
        }

        assert _names(board_order(columns, proxies)) == [
            "Backlog",
            "Added later",
            "Sprint 1",
            "Sprint 2",
        ]

    def test_a_subproject_column_keeps_its_sequence(self):
        columns = [
            _column(2, "Backend", 1, proxy="PHID-PROJ-sub"),
            _column(3, "Done", 2),
            _column(1, "Backlog", 0),
        ]
        proxies = {"PHID-PROJ-sub": {"fields": {"milestone": None}}}

        assert _names(board_order(columns, proxies)) == ["Backlog", "Backend", "Done"]


class TestFetchBoardColumns:
    def test_proxies_are_looked_up_in_every_status(self, phab):
        columns, proxies = fetch_board_columns(phab, "PHID-PROJ-12")

        assert _names(columns) == [*BOARD_ORDER, "Won't Do", "Sprint 1"]
        assert list(proxies) == ["PHID-PROJ-13"]
        constraints = phab.project.search.call_args.kwargs["constraints"]
        assert constraints == {"phids": ["PHID-PROJ-13"], "status": "all"}

    def test_a_board_without_proxies_costs_one_request(self, phab):
        phab.boards["PHID-PROJ-14"] = DEVELOPMENT_BOARD[2:]

        fetch_board_columns(phab, "PHID-PROJ-14")

        phab.project.search.assert_not_called()

    def test_several_boards_apart(self, phab):
        phab.boards["PHID-PROJ-14"] = DEVELOPMENT_BOARD[2:]

        columns, _ = fetch_boards_columns(phab, ["PHID-PROJ-12", "PHID-PROJ-14"])

        assert _names(columns["PHID-PROJ-14"]) == BOARD_ORDER
        assert len(columns["PHID-PROJ-12"]) == len(DEVELOPMENT_BOARD)

    def test_a_failure_is_not_an_empty_board(self, phab):
        phab.project.column.search.side_effect = RuntimeError("boom")

        with pytest.raises(PhabfiveDataException, match="workboard columns"):
            fetch_board_columns(phab, "PHID-PROJ-12")


class TestFormatColumn:
    def test_a_column_of_the_boards_own(self):
        assert format_column(DEVELOPMENT_BOARD[-1]) == {
            "Name": "Backlog",
            "Sequence": 0,
            "Hidden": False,
            "Default": True,
            "Proxy": None,
        }

    def test_a_milestone_is_told_apart_by_its_id(self, phab):
        _, proxies = fetch_board_columns(phab, "PHID-PROJ-12")

        assert format_column(DEVELOPMENT_BOARD[0], proxies)["Proxy"] == (
            "Sprint 1 (ID 13)"
        )

    def test_a_proxy_the_viewer_cannot_see_is_its_phid(self):
        assert format_column(DEVELOPMENT_BOARD[0])["Proxy"] == "PHID-PROJ-13"


class TestShowColumns:
    def test_active_by_default_in_board_order(self, phab):
        result = _app(phab).show(["#development"])

        columns = result["projects"][0]["Columns"]
        assert [column["Name"] for column in columns] == [*BOARD_ORDER, "Sprint 1"]
        assert result["hidden_unknown"] == []

    def test_hidden_lists_only_the_hidden_ones(self, phab):
        result = _app(phab).show(["#development"], columns="hidden")

        assert result["projects"][0]["Columns"] == [
            {
                "Name": "Won't Do",
                "Sequence": 5,
                "Hidden": True,
                "Default": False,
                "Proxy": None,
            }
        ]

    def test_any_lists_both(self, phab):
        result = _app(phab).show(["#development"], columns="any")

        columns = result["projects"][0]["Columns"]
        assert [column["Name"] for column in columns] == [
            *BOARD_ORDER,
            "Won't Do",
            "Sprint 1",
        ]

    def test_none_leaves_the_section_out_and_asks_nothing(self, phab):
        result = _app(phab).show(["#development"], columns="none")

        assert "Columns" not in result["projects"][0]
        phab.project.column.search.assert_not_called()

    def test_an_unknown_scope_is_refused(self, phab):
        with pytest.raises(PhabfiveInputException, match="columns must be one of"):
            _app(phab).show(["#development"], columns="all")

    def test_no_workboard_is_none(self, phab):
        result = _app(phab).show(["#humans"])

        assert result["projects"][0]["Columns"] is None

    def test_no_column_of_the_kind_is_an_empty_list(self, phab):
        phab.boards["PHID-PROJ-14"] = [_column(1, "Backlog", 0, hidden=True)]

        result = _app(phab).show(["#qa"])

        assert result["projects"][0]["Columns"] == []

    def test_one_request_for_every_board(self, phab):
        phab.boards["PHID-PROJ-14"] = DEVELOPMENT_BOARD[2:]

        result = _app(phab).show(["#development", "#qa", "#humans"])

        assert phab.project.column.search.call_count == 1
        names = [
            [column["Name"] for column in record["Columns"] or []]
            for record in result["projects"]
        ]
        assert names == [[*BOARD_ORDER, "Sprint 1"], BOARD_ORDER, []]

    def test_cli(self, phab, restore_output_format):
        result = _invoke(
            phab, ["--format=json", "project", "show", "#development,#humans"]
        )

        assert result.exit_code == 0
        records = json.loads(result.stdout)
        assert records[0]["Columns"][0] == {
            "Name": "Backlog",
            "Sequence": 0,
            "Hidden": False,
            "Default": True,
            "Proxy": None,
        }
        assert records[1]["Columns"] is None
        assert result.stderr == ""

    def test_cli_refuses_an_unknown_scope(self, phab):
        result = _invoke(phab, ["project", "show", "#development", "--columns=all"])

        assert result.exit_code == 1
        assert "--columns must be one of: active, hidden, any, none" in result.stderr
        phab.project.search.assert_not_called()


class TestGetColumnInfo:
    def test_board_order_and_what_navigation_needs(self, phab):
        info = get_column_info(phab, "PHID-PROJ-12")

        assert [column["name"] for column in info.values()] == [
            *BOARD_ORDER,
            "Won't Do",
            "Sprint 1",
        ]
        assert info["PHID-PCOL-46"]["isHidden"] is True
        assert info["PHID-PCOL-47"]["proxyPHID"] == "PHID-PROJ-13"
        assert info["PHID-PCOL-47"]["position"] == 6
        assert info["PHID-PCOL-16"]["isDefaultColumn"] is True


class TestNavigateColumn:
    """forward/backward step along the board's own visible columns."""

    def _navigate(self, phab, current, column):
        from phabfive.maniphest import Maniphest

        with mock.patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
            maniphest = Maniphest()
        maniphest.phab = phab

        task = {
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-12": {"columns": [{"phid": f"PHID-PCOL-{current}"}]}
                    }
                }
            }
        }
        return maniphest._navigate_column("6", task, column, "PHID-PROJ-12")

    def test_forward_from_the_last_column_stays(self, phab):
        """Not into the hidden column, and not on into the milestone."""
        assert self._navigate(phab, 20, "forward") == "PHID-PCOL-20"

    def test_forward_from_a_hidden_column_stays(self, phab):
        assert self._navigate(phab, 46, "forward") == "PHID-PCOL-46"

    def test_backward_from_a_hidden_column(self, phab):
        assert self._navigate(phab, 46, "backward") == "PHID-PCOL-20"

    def test_backward_from_the_milestone_column(self, phab):
        assert self._navigate(phab, 47, "backward") == "PHID-PCOL-20"

    def test_forward_in_board_order(self, phab):
        assert self._navigate(phab, 16, "forward") == "PHID-PCOL-17"

    def test_a_hidden_column_is_still_found_by_name(self, phab):
        assert self._navigate(phab, 20, "won't do") == "PHID-PCOL-46"

    def test_a_milestone_column_is_found_by_name(self, phab):
        assert self._navigate(phab, 20, "Sprint 1") == "PHID-PCOL-47"

    def test_not_found_lists_the_board_in_order_and_points_to_show(self, phab):
        with pytest.raises(PhabfiveNotFoundException) as error:
            self._navigate(phab, 20, "Review")

        message = str(error.value)
        assert f"Available: {[*BOARD_ORDER, 'Sprint 1']}" in message
        assert "Won't Do" not in message
        assert message.endswith("phabfive project show PHID-PROJ-12")


def _legacy(column):
    """The column as Phabricator and Phorge before 2023.32 report it."""
    fields = {
        key: value
        for key, value in column["fields"].items()
        if key not in ("sequence", "isHidden", "isDefaultColumn")
    }
    return {**column, "fields": fields}


class TestServersWithoutColumnOrder:
    """Phabricator and Phorge before 2023.32: no sequence, isHidden, isDefaultColumn."""

    @pytest.fixture
    def phab(self):
        return ColumnPhab({"PHID-PROJ-12": [_legacy(c) for c in DEVELOPMENT_BOARD]})

    def test_creation_order_with_the_milestone_last(self, phab):
        columns, _ = fetch_board_columns(phab, "PHID-PROJ-12")

        assert _names(columns) == [*BOARD_ORDER, "Won't Do", "Sprint 1"]

    def test_unknown_is_null_not_false(self, phab):
        result = _app(phab).show(["#development"])

        columns = result["projects"][0]["Columns"]
        # Nothing says which column is hidden, so every one is listed
        assert len(columns) == len(DEVELOPMENT_BOARD)
        assert columns[0] == {
            "Name": "Backlog",
            "Sequence": None,
            "Hidden": None,
            "Default": None,
            "Proxy": None,
        }
        assert result["hidden_unknown"] == ["#development"]

    def test_any_needs_no_warning(self, phab):
        result = _app(phab).show(["#development"], columns="any")

        assert len(result["projects"][0]["Columns"]) == len(DEVELOPMENT_BOARD)
        assert result["hidden_unknown"] == []

    def test_hidden_is_refused_rather_than_guessed(self, phab):
        with pytest.raises(PhabfiveDataException, match="does not report which"):
            _app(phab).show(["#development"], columns="hidden")

    def test_cli_warns_that_every_column_is_listed(self, phab, restore_output_format):
        result = _invoke(phab, ["--format=json", "project", "show", "#development"])

        assert result.exit_code == 0
        assert len(json.loads(result.stdout)[0]["Columns"]) == len(DEVELOPMENT_BOARD)
        assert "#development: the server does not report which" in result.stderr

    def test_a_step_is_refused_rather_than_guessed(self, phab):
        with pytest.raises(PhabfiveDataException, match="does not report the order"):
            TestNavigateColumn()._navigate(phab, 16, "forward")

    def test_a_name_still_works(self, phab):
        assert TestNavigateColumn()._navigate(phab, 16, "Done") == "PHID-PCOL-20"
