# -*- coding: utf-8 -*-

"""The `table` format flattens a record into columns (#376).

A grid has one cell per field and nowhere to put a subtree, so `table` is
the one format that compresses. These tests pin the rule it compresses by,
because the whole point of stating one rule is that `repo list`,
`uri list`, `maniphest search` and `paste search` all reach it with no
column list of their own.
"""

from io import StringIO

import pytest
from rich.console import Console

from phabfive.table import (
    CELL_WIDTH,
    MIN_COLUMN,
    _fit,
    build_table,
    display_records_table,
)


def _render(records, max_width=None, width=400):
    """The table as plain text, one string per line, right-stripped."""
    console = Console(file=StringIO(), width=width, force_terminal=False, no_color=True)
    console.print(build_table(records, max_width=max_width))

    return [line.rstrip() for line in console.file.getvalue().splitlines()]


def _printed(records):
    """What `--format=table` actually writes, guard and all."""
    console = Console(file=StringIO(), width=400, force_terminal=False, no_color=True)
    display_records_table(console, records)

    return console.file.getvalue()


def _columns(records, **kwargs):
    header = _render(records, **kwargs)[0]

    return header.split()


class TestFlattening:
    """One rule, applied to whatever record it is handed."""

    def test_a_nested_section_contributes_its_leaves_not_itself(self):
        rows = _render([{"Repository": {"Name": "phabfive", "VCS": "git"}}])

        assert rows[0].split() == ["Name", "VCS"]
        assert rows[1].split() == ["phabfive", "git"]

    def test_a_scalar_beside_a_section_is_a_column_too(self):
        assert _columns([{"Repository": {"Name": "x"}, "Space": "S1"}]) == [
            "Name",
            "Space",
        ]

    def test_a_list_is_one_column_not_one_column_per_item(self):
        rows = _render([{"Name": "phabfive", "Branches": ["main", "next"]}])

        assert rows[0].split() == ["Name", "Branches"]
        assert "main, next" in rows[1]

    def test_a_list_of_records_reads_as_their_first_field(self):
        rows = _render(
            [
                {
                    "Name": "phabfive",
                    "URIs": [
                        {"URI": "git@example.com:a.git", "I/O": "observe"},
                        {"URI": "git@example.com:b.git", "I/O": "none"},
                    ],
                }
            ]
        )

        assert "git@example.com:a.git, git@example.com:b.git" in rows[1]
        assert "observe" not in rows[1]

    def test_an_empty_section_says_nothing_and_so_is_dropped(self):
        assert _columns([{"Name": "phabfive", "Boards": {}}]) == ["Name"]


class TestResolvedValues:
    """Raw/Default/Effective is one value, not three columns (#375).

    A URI publishes its I/O and its display as what is written on it, what
    it would inherit and what is in force. Flattening that as three leaves
    apiece spends six columns of a seven-field record on two fields, and
    none of the six says which of the other two it answers to - so the
    grid says it in the cell instead.
    """

    def _uri(self, io_raw, io_effective):
        return {
            "URI": "git@example.com:a.git",
            "I/O": {"Raw": io_raw, "Default": "none", "Effective": io_effective},
        }

    def test_a_resolved_section_is_one_column_named_for_itself(self):
        assert _columns([self._uri("observe", "observe")]) == ["URI", "I/O"]

    def test_a_value_written_on_the_record_says_it_was_set(self):
        rows = _render([self._uri("observe", "observe")])

        assert "observe (set)" in rows[1]

    def test_an_inherited_value_says_which_one_is_in_force(self):
        rows = _render([self._uri("default", "readwrite")])

        assert "readwrite (default)" in rows[1]

    def test_a_section_with_no_raw_at_all_is_read_as_inherited(self):
        rows = _render([self._uri(None, "readwrite")])

        assert "readwrite (default)" in rows[1]

    def test_a_section_resolving_to_nothing_is_an_empty_cell(self):
        """And so drops its column, the same as any other empty value."""
        assert _columns([self._uri("default", None)]) == ["URI"]

    def test_two_resolved_sections_stay_two_columns(self):
        rows = _render(
            [
                {
                    "URI": "git@example.com:a.git",
                    "I/O": {
                        "Raw": "observe",
                        "Default": "none",
                        "Effective": "observe",
                    },
                    "Display": {
                        "Raw": "default",
                        "Default": "never",
                        "Effective": "never",
                    },
                }
            ]
        )

        assert rows[0].split() == ["URI", "I/O", "Display"]
        assert "observe (set)" in rows[1]
        assert "never (default)" in rows[1]

    def test_a_section_that_is_not_one_flattens_as_usual(self):
        """The rule reads the three keys, not the name of the section."""
        assert _columns([{"I/O": {"Raw": "observe", "Effective": "observe"}}]) == [
            "Raw",
            "Effective",
        ]


class TestColumnNames:
    """Named by the leaf, grown leftwards only to stay unique."""

    def test_the_leaf_key_names_the_column(self):
        assert _columns([{"Repository": {"Monogram": "R5"}}]) == ["Monogram"]

    def test_a_clash_grows_both_names_leftwards(self):
        rows = _render(
            [
                {
                    "Boards": {
                        "Infrastructure": {"Column": "Backlog"},
                        "Security": {"Column": "Review"},
                    }
                }
            ]
        )

        assert rows[0].split() == [
            "Infrastructure",
            "Column",
            "Security",
            "Column",
        ]

    def test_one_board_needs_no_qualifying(self):
        assert _columns([{"Boards": {"Infrastructure": {"Column": "Backlog"}}}]) == [
            "Column"
        ]


class TestWhatIsLeftOut:
    """Internals, links, and columns nothing filled in."""

    def test_an_internal_key_is_not_a_column(self):
        assert _columns([{"_url": "http://x/R5", "Name": "phabfive"}]) == ["Name"]

    def test_a_link_is_not_a_column_of_urls(self):
        rows = _render([{"Link": "http://phorge.localhost/R5", "Name": "phabfive"}])

        assert rows[0].split() == ["Name"]
        assert "http://" not in "\n".join(rows)

    def test_a_link_becomes_the_hyperlink_on_the_row(self):
        """Asked of the cell rather than of the escape codes: whether a
        terminal is given OSC-8 is Rich's business, and the legacy Windows
        console is not given any."""
        table = build_table(
            [{"Link": "http://phorge.localhost/R5", "Name": "phabfive"}]
        )
        [cell] = list(table.columns[0].cells)

        assert cell.style == "link http://phorge.localhost/R5"
        assert cell.plain == "phabfive"

    def test_a_column_empty_in_every_row_is_dropped(self):
        records = [
            {"Name": "a", "Callsign": None},
            {"Name": "b", "Callsign": ""},
        ]

        assert _columns(records) == ["Name"]

    def test_a_column_one_row_filled_in_is_kept(self):
        records = [
            {"Name": "a", "Callsign": None},
            {"Name": "b", "Callsign": "GUNNAR"},
        ]

        assert _columns(records) == ["Name", "Callsign"]

    def test_false_is_a_value_and_keeps_its_column(self):
        rows = _render([{"URI": "git@example.com:a.git", "Disabled": False}])

        assert rows[0].split() == ["URI", "Disabled"]
        assert rows[1].split()[-1] == "false"

    def test_a_record_with_nothing_to_show_prints_nothing(self):
        assert _printed([{"Link": "http://x/R5", "Callsign": None}]) == ""


class TestCells:
    """One row is one line, whatever the record put in it."""

    def test_a_boolean_is_spelled_the_way_yaml_spells_it(self):
        rows = _render([{"Hosted": True, "Importing": False}])

        assert rows[1].split() == ["true", "false"]

    def test_a_long_value_is_cut_rather_than_carried(self):
        rows = _render([{"Description": "x" * 200}])

        assert len(rows[1]) == CELL_WIDTH
        assert rows[1].endswith("...")

    def test_a_uri_survives_whole(self):
        uri = "ssh://phorge@phorge.localhost/source/gunnar-firmware.git"

        assert _render([{"URI": uri}])[1] == uri

    def test_a_multi_line_value_keeps_its_first_line_and_says_so(self):
        rows = _render([{"Description": "first line\nsecond line"}])

        assert rows[1] == "first line..."

    def test_a_row_is_always_one_line(self):
        rows = _render([{"Description": "a\nb\nc\nd"}, {"Description": "e"}])

        assert len(rows) == 3


class TestFitting:
    """A terminal has a width; the widest column pays for it."""

    def test_the_widest_column_is_shrunk_first(self):
        assert _fit([10, 40, 10], 40) == [10, 20, 10]

    def test_nothing_is_shrunk_when_it_already_fits(self):
        assert _fit([10, 20], 100) == [10, 20]

    def test_shrinking_stops_at_a_readable_minimum(self):
        assert _fit([20, 20], 4) == [MIN_COLUMN, MIN_COLUMN]

    def test_a_fitted_row_still_fits(self):
        records = [{"Name": "phabfive", "Description": "y" * 200, "VCS": "git"}]
        rows = _render(records, max_width=40)

        assert max(len(row) for row in rows) <= 40

    def test_fitting_costs_the_widest_column_and_not_the_short_ones(self):
        records = [{"Name": "phabfive", "Description": "y" * 200, "VCS": "git"}]
        rows = _render(records, max_width=40)

        assert rows[0].split() == ["Name", "Description", "VCS"]
        assert rows[1].startswith("phabfive")
        assert rows[1].endswith("git")
        assert "..." in rows[1]

    @pytest.mark.parametrize("records", [[], [{}]])
    def test_nothing_to_show_is_not_an_error(self, records):
        assert _printed(records) == ""


class TestTheAppsThatAdoptedIt:
    """Global means more than diffusion: maniphest and paste reach the
    same renderer with no column list of their own."""

    TASK = {
        "_url": "http://phorge.localhost/T1",
        "_link": "http://phorge.localhost/T1",
        "_assignee": "tommy.svensson",
        "Task": {"Name": "Harden SSH", "Status": "Open", "Priority": "High"},
        "Boards": {"Security": {"Column": "Backlog", "_column_phid": "PHID-PCOL-1"}},
    }

    def _tasks(self, output_format, tabular):
        from unittest.mock import MagicMock

        from phabfive.display import display_tasks

        console = Console(
            file=StringIO(), width=400, force_terminal=False, no_color=True
        )
        instance = MagicMock()
        instance.get_console.return_value = console
        instance.url = "http://phorge.localhost"
        instance.format_link = lambda url, text, show_url=False: text

        display_tasks({"tasks": [self.TASK]}, output_format, instance, tabular=tabular)

        return [line.rstrip() for line in console.file.getvalue().splitlines()]

    def test_maniphest_search_gets_a_grid(self):
        rows = self._tasks("table", tabular=True)

        assert rows[0].split() == [
            "Name",
            "Status",
            "Priority",
            "Assignee",
            "Column",
        ]
        assert rows[1].startswith("Harden SSH")

    def test_a_board_column_phid_is_internal_and_stays_out(self):
        assert "PHID-PCOL-1" not in "\n".join(self._tasks("table", tabular=True))

    def test_maniphest_show_falls_back_to_rich(self):
        rows = self._tasks("table", tabular=False)

        assert rows[0] == "- Link: http://phorge.localhost/T1"

    def _paste_search(self, output_format):
        from unittest.mock import MagicMock, patch

        from typer.testing import CliRunner

        from phabfive.cli import app

        paste = MagicMock()
        paste.get_console.return_value = Console(
            force_terminal=False, no_color=True, width=400
        )
        paste.get_pastes.return_value = [
            {"id": 1, "fields": {"title": "deploy notes"}},
            {"id": 2, "fields": {"title": "nginx config"}},
        ]

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = CliRunner().invoke(
                app, [f"--format={output_format}", "paste", "search", "e"]
            )

        assert result.exit_code == 0, result.output

        return [line.rstrip() for line in result.output.splitlines() if line.strip()]

    def test_paste_search_gets_a_grid(self):
        rows = self._paste_search("table")

        assert rows[0].split() == ["id", "title"]
        assert rows[1].split() == ["P1", "deploy", "notes"]
        assert len(rows) == 3

    def test_paste_search_still_prints_bare_lines_for_rich(self):
        assert self._paste_search("rich") == ["P1 deploy notes", "P2 nginx config"]
