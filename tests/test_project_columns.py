# -*- coding: utf-8 -*-

"""Workboard columns: `project show --show-columns`, and `--column` on edit (#588)."""

# python std lib
import json

# 3rd party imports
from unittest import mock

import pytest

# phabfive imports
from phabfive.exceptions import PhabfiveDataException, PhabfiveNotFoundException
from phabfive.maniphest.fetchers import get_column_info
from phabfive.project.fetchers import board_order, fetch_board_columns
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
            column
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
    def test_not_asked_for_no_section_and_no_request(self, phab):
        result = _app(phab).show(["#development"])

        assert "Columns" not in result["projects"][0]
        phab.project.column.search.assert_not_called()

    def test_board_order_without_hidden_columns(self, phab):
        result = _app(phab).show(["#development"], show_columns=True)

        columns = result["projects"][0]["Columns"]
        assert [column["Name"] for column in columns] == [*BOARD_ORDER, "Sprint 1"]
        assert result["no_workboard"] == []

    def test_show_hidden_includes_them_and_implies_columns(self, phab):
        result = _app(phab).show(["#development"], show_hidden=True)

        columns = result["projects"][0]["Columns"]
        assert [column["Name"] for column in columns] == [
            *BOARD_ORDER,
            "Won't Do",
            "Sprint 1",
        ]
        assert columns[-2]["Hidden"] is True

    def test_no_workboard_is_none_and_reported(self, phab):
        result = _app(phab).show(["#humans"], show_columns=True)

        assert result["projects"][0]["Columns"] is None
        assert result["no_workboard"] == ["#humans"]

    def test_only_hidden_columns_is_an_empty_list(self, phab):
        phab.boards["PHID-PROJ-14"] = [_column(1, "Backlog", 0, hidden=True)]

        result = _app(phab).show(["#qa"], show_columns=True)

        assert result["projects"][0]["Columns"] == []
        assert result["no_workboard"] == []

    def test_cli(self, phab, restore_output_format):
        result = _invoke(
            phab,
            ["--format=json", "project", "show", "#development,#humans"]
            + ["--show-columns"],
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
        assert "#humans has no workboard" in result.stderr


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
        assert "project show PHID-PROJ-12 --show-columns" in message


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
        result = _app(phab).show(["#development"], show_columns=True)

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

    def test_a_step_is_refused_rather_than_guessed(self, phab):
        with pytest.raises(PhabfiveDataException, match="does not report the order"):
            TestNavigateColumn()._navigate(phab, 16, "forward")

    def test_a_name_still_works(self, phab):
        assert TestNavigateColumn()._navigate(phab, 16, "Done") == "PHID-PCOL-20"
