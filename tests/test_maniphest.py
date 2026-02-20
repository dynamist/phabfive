# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveException,
)

# phabfive imports
from phabfive.maniphest import (
    Maniphest,
    _build_dependency_graph,
    _detect_circular_dependencies,
    _extract_variable_dependencies,
    _render_variables_with_dependency_resolution,
    _topological_sort,
    parse_time_with_unit,
)
from phabfive.column_transitions import (
    ColumnPattern,
    _parse_single_condition,
    parse_column_patterns,
)


class TestExtractVariableDependencies:
    def test_simple_variable_reference(self):
        result = _extract_variable_dependencies("{{ foo }}")
        assert result == {"foo"}

    def test_multiple_variables(self):
        result = _extract_variable_dependencies("{{ foo }} and {{ bar }}")
        assert result == {"foo", "bar"}

    def test_variable_with_filter(self):
        result = _extract_variable_dependencies("{{ foo | upper }}")
        assert result == {"foo"}

    def test_variable_with_expression(self):
        result = _extract_variable_dependencies("{{ foo + bar }}")
        assert result == {"foo", "bar"}

    def test_no_variables(self):
        result = _extract_variable_dependencies("plain text")
        assert result == set()

    def test_invalid_template(self):
        # Invalid syntax should return empty set with warning
        result = _extract_variable_dependencies("{{ unclosed")
        assert result == set()


class TestBuildDependencyGraph:
    def test_simple_dependency_chain(self):
        variables = {
            "a": "value_a",
            "b": "{{ a }}",
            "c": "{{ b }}",
        }
        graph = _build_dependency_graph(variables)
        assert graph == {
            "a": set(),
            "b": {"a"},
            "c": {"b"},
        }

    def test_multiple_dependencies(self):
        variables = {
            "a": "value_a",
            "b": "value_b",
            "c": "{{ a }} and {{ b }}",
        }
        graph = _build_dependency_graph(variables)
        assert graph == {
            "a": set(),
            "b": set(),
            "c": {"a", "b"},
        }

    def test_non_string_values(self):
        variables = {
            "a": 123,
            "b": ["list"],
            "c": "{{ a }}",
        }
        graph = _build_dependency_graph(variables)
        assert graph == {
            "a": set(),
            "b": set(),
            "c": set(),  # Reference to non-existent string variable filtered out
        }

    def test_reference_to_nonexistent_variable(self):
        variables = {
            "a": "{{ nonexistent }}",
        }
        graph = _build_dependency_graph(variables)
        assert graph == {
            "a": set(),  # nonexistent is filtered out
        }

    def test_empty_variables(self):
        variables = {}
        graph = _build_dependency_graph(variables)
        assert graph == {}


class TestDetectCircularDependencies:
    def test_no_cycle_linear(self):
        graph = {
            "a": set(),
            "b": {"a"},
            "c": {"b"},
        }
        has_cycle, cycle_path = _detect_circular_dependencies(graph)
        assert has_cycle is False
        assert cycle_path == []

    def test_simple_two_node_cycle(self):
        graph = {
            "a": {"b"},
            "b": {"a"},
        }
        has_cycle, cycle_path = _detect_circular_dependencies(graph)
        assert has_cycle is True
        assert len(cycle_path) > 0
        # Cycle should contain both nodes
        assert "a" in cycle_path
        assert "b" in cycle_path

    def test_self_reference_cycle(self):
        graph = {
            "a": {"a"},
        }
        has_cycle, cycle_path = _detect_circular_dependencies(graph)
        assert has_cycle is True
        assert "a" in cycle_path

    def test_three_node_cycle(self):
        graph = {
            "a": {"b"},
            "b": {"c"},
            "c": {"a"},
        }
        has_cycle, cycle_path = _detect_circular_dependencies(graph)
        assert has_cycle is True
        assert len(cycle_path) > 0
        # All three nodes should be in the cycle
        assert "a" in cycle_path
        assert "b" in cycle_path
        assert "c" in cycle_path

    def test_four_node_cycle(self):
        graph = {
            "a": {"b"},
            "b": {"c"},
            "c": {"d"},
            "d": {"a"},
        }
        has_cycle, cycle_path = _detect_circular_dependencies(graph)
        assert has_cycle is True
        assert len(cycle_path) >= 4

    def test_independent_subgraphs_no_cycle(self):
        graph = {
            "a": set(),
            "b": {"a"},
            "c": set(),
            "d": {"c"},
        }
        has_cycle, cycle_path = _detect_circular_dependencies(graph)
        assert has_cycle is False
        assert cycle_path == []

    def test_cycle_in_one_branch(self):
        graph = {
            "a": set(),
            "b": {"a"},
            "c": {"d"},
            "d": {"c"},
        }
        has_cycle, cycle_path = _detect_circular_dependencies(graph)
        assert has_cycle is True
        # Cycle should contain c and d
        assert "c" in cycle_path
        assert "d" in cycle_path


class TestTopologicalSort:
    def test_no_dependencies(self):
        graph = {
            "a": set(),
            "b": set(),
            "c": set(),
        }
        result = _topological_sort(graph)
        # All variables should be present
        assert set(result) == {"a", "b", "c"}

    def test_linear_chain(self):
        graph = {
            "a": set(),
            "b": {"a"},
            "c": {"b"},
        }
        result = _topological_sort(graph)
        # a must come before b, b must come before c
        assert result.index("a") < result.index("b")
        assert result.index("b") < result.index("c")

    def test_diamond_pattern(self):
        graph = {
            "a": set(),
            "b": {"a"},
            "c": {"a"},
            "d": {"b", "c"},
        }
        result = _topological_sort(graph)
        # a must come before b, c, and d
        assert result.index("a") < result.index("b")
        assert result.index("a") < result.index("c")
        assert result.index("a") < result.index("d")
        # b and c must come before d
        assert result.index("b") < result.index("d")
        assert result.index("c") < result.index("d")

    def test_multiple_independent_chains(self):
        graph = {
            "a": set(),
            "b": {"a"},
            "c": set(),
            "d": {"c"},
        }
        result = _topological_sort(graph)
        # Within each chain, order must be preserved
        assert result.index("a") < result.index("b")
        assert result.index("c") < result.index("d")

    def test_complex_tree(self):
        graph = {
            "root": set(),
            "branch1": {"root"},
            "branch2": {"root"},
            "leaf1": {"branch1"},
            "leaf2": {"branch1", "branch2"},
        }
        result = _topological_sort(graph)
        # root must come first
        assert result.index("root") < result.index("branch1")
        assert result.index("root") < result.index("branch2")
        # branches before leaves
        assert result.index("branch1") < result.index("leaf1")
        assert result.index("branch1") < result.index("leaf2")
        assert result.index("branch2") < result.index("leaf2")


class TestRenderVariablesWithDependencyResolution:
    def test_simple_substitution(self):
        variables = {
            "name": "John",
            "greeting": "Hello {{ name }}",
        }
        result = _render_variables_with_dependency_resolution(variables)
        assert result["name"] == "John"
        assert result["greeting"] == "Hello John"

    def test_multilevel_nesting(self):
        variables = {
            "base": "world",
            "level1": "Hello {{ base }}",
            "level2": "{{ level1 }}!",
            "level3": "Message: {{ level2 }}",
        }
        result = _render_variables_with_dependency_resolution(variables)
        assert result["base"] == "world"
        assert result["level1"] == "Hello world"
        assert result["level2"] == "Hello world!"
        assert result["level3"] == "Message: Hello world!"

    def test_order_independent_definitions(self):
        # Variables defined in reverse dependency order
        variables = {
            "z_greeting": "Hello {{ a_name }}",
            "a_name": "Alice",
        }
        result = _render_variables_with_dependency_resolution(variables)
        assert result["a_name"] == "Alice"
        assert result["z_greeting"] == "Hello Alice"

    def test_non_string_values_unchanged(self):
        variables = {
            "num": 42,
            "list": [1, 2, 3],
            "text": "plain",
        }
        result = _render_variables_with_dependency_resolution(variables)
        assert result["num"] == 42
        assert result["list"] == [1, 2, 3]
        assert result["text"] == "plain"

    def test_circular_dependency_raises_exception(self):
        variables = {
            "a": "{{ b }}",
            "b": "{{ a }}",
        }
        with pytest.raises(PhabfiveDataException) as exc_info:
            _render_variables_with_dependency_resolution(variables)
        assert "Circular reference" in str(exc_info.value)

    def test_self_reference_raises_exception(self):
        variables = {
            "a": "{{ a }}",
        }
        with pytest.raises(PhabfiveDataException) as exc_info:
            _render_variables_with_dependency_resolution(variables)
        assert "Circular reference" in str(exc_info.value)

    def test_indirect_circular_reference_raises_exception(self):
        variables = {
            "a": "{{ b }}",
            "b": "{{ c }}",
            "c": "{{ a }}",
        }
        with pytest.raises(PhabfiveDataException) as exc_info:
            _render_variables_with_dependency_resolution(variables)
        assert "Circular reference" in str(exc_info.value)
        # Error message should show the cycle path
        error_msg = str(exc_info.value)
        assert "a" in error_msg or "b" in error_msg or "c" in error_msg

    def test_complex_real_world_scenario(self):
        variables = {
            "env": "production",
            "domain": "example.com",
            "protocol": "https",
            "base_url": "{{ protocol }}://{{ domain }}",
            "api_endpoint": "{{ base_url }}/api/v1",
            "webhook_url": "{{ api_endpoint }}/webhooks",
            "port": 443,
            "full_address": "{{ base_url }}:{{ port }}",
        }
        result = _render_variables_with_dependency_resolution(variables)
        assert result["env"] == "production"
        assert result["domain"] == "example.com"
        assert result["protocol"] == "https"
        assert result["base_url"] == "https://example.com"
        assert result["api_endpoint"] == "https://example.com/api/v1"
        assert result["webhook_url"] == "https://example.com/api/v1/webhooks"
        assert result["port"] == 443
        assert result["full_address"] == "https://example.com:443"


class TestFetchProjectNamesForBoards:
    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_fetch_project_names(self, mock_init):
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()

        tasks_data = [
            {
                "attachments": {
                    "columns": {
                        "boards": {
                            "PHID-PROJ-123": {"columns": []},
                            "PHID-PROJ-456": {"columns": []},
                        }
                    }
                }
            }
        ]

        maniphest.phab.project.search.return_value = {
            "data": [
                {"phid": "PHID-PROJ-123", "fields": {"name": "Project A"}},
                {"phid": "PHID-PROJ-456", "fields": {"name": "Project B"}},
            ]
        }

        result = maniphest._fetch_project_names_for_boards(tasks_data)
        assert result == {"PHID-PROJ-123": "Project A", "PHID-PROJ-456": "Project B"}

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_fetch_project_names_with_list_boards(self, mock_init):
        """Test handling when boards is a list (empty or non-empty)."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()

        tasks_data = [
            {
                "attachments": {
                    "columns": {
                        "boards": []  # List format instead of dict
                    }
                }
            }
        ]

        # Should return empty dict when boards is a list
        result = maniphest._fetch_project_names_for_boards(tasks_data)
        assert result == {}


class TestBuildTaskBoards:
    """Test that _build_task_boards creates correct data structures."""

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_build_with_empty_project_names(self, mock_init):
        mock_init.return_value = None
        maniphest = Maniphest()

        boards = {"PHID-PROJ-123": {"columns": [{"name": "Backlog"}, {"name": "Done"}]}}

        result = maniphest._build_task_boards(boards, {})
        # Should return dict with Unknown project name
        assert isinstance(result, dict)
        assert "Unknown" in result
        assert result["Unknown"]["Column"] == "Backlog"  # First column only

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_build_with_project_names(self, mock_init):
        mock_init.return_value = None
        maniphest = Maniphest()

        boards = {"PHID-PROJ-123": {"columns": [{"name": "Backlog"}]}}
        project_names = {"PHID-PROJ-123": "Project A"}

        result = maniphest._build_task_boards(boards, project_names)
        # Should return dict with proper project name
        assert isinstance(result, dict)
        assert "Project A" in result
        assert result["Project A"]["Column"] == "Backlog"

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_build_with_list_boards(self, mock_init):
        """Test that list boards format returns empty dict."""
        mock_init.return_value = None
        maniphest = Maniphest()

        boards = []  # List format

        result = maniphest._build_task_boards(boards, {})
        # Should return empty dict
        assert result == {}

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_build_with_list_boards_single_project(self, mock_init):
        """Test that list boards format returns empty dict."""
        mock_init.return_value = None
        maniphest = Maniphest()

        boards = []  # List format

        result = maniphest._build_task_boards(boards, {})
        # Should return empty dict
        assert result == {}


class TestParseSingleCondition:
    def test_parse_backward(self):
        result = _parse_single_condition("backward")
        assert result == {"type": "backward"}

    def test_parse_forward(self):
        result = _parse_single_condition("forward")
        assert result == {"type": "forward"}

    def test_parse_from_simple(self):
        result = _parse_single_condition("from:In Progress")
        assert result == {"type": "from", "column": "In Progress"}

    def test_parse_from_with_forward(self):
        result = _parse_single_condition("from:Up Next:forward")
        assert result == {"type": "from", "column": "Up Next", "direction": "forward"}

    def test_parse_from_with_backward(self):
        result = _parse_single_condition("from:Done:backward")
        assert result == {"type": "from", "column": "Done", "direction": "backward"}

    def test_parse_to(self):
        result = _parse_single_condition("to:Done")
        assert result == {"type": "to", "column": "Done"}

    def test_parse_in(self):
        result = _parse_single_condition("in:Blocked")
        assert result == {"type": "in", "column": "Blocked"}

    def test_parse_been(self):
        result = _parse_single_condition("been:Blocked")
        assert result == {"type": "been", "column": "Blocked"}

    def test_parse_never(self):
        result = _parse_single_condition("never:Blocked")
        assert result == {"type": "never", "column": "Blocked"}

    def test_invalid_type(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("invalid:Column")
        assert "Invalid column condition type" in str(exc.value)

    def test_missing_colon(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("notavalidpattern")
        assert "Invalid column condition syntax" in str(exc.value)

    def test_empty_column_name(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("from:")
        assert "Empty column name" in str(exc.value)

    def test_direction_on_non_from(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("to:Done:forward")
        assert "Direction modifier only allowed for 'from' patterns" in str(exc.value)

    def test_invalid_direction(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("from:Column:invalid")
        assert "Invalid direction" in str(exc.value)

    def test_parse_not_in(self):
        result = _parse_single_condition("not:in:Done")
        assert result == {"type": "in", "column": "Done", "negated": True}

    def test_parse_not_from(self):
        result = _parse_single_condition("not:from:Backlog")
        assert result == {"type": "from", "column": "Backlog", "negated": True}

    def test_parse_not_from_with_direction(self):
        result = _parse_single_condition("not:from:Backlog:forward")
        assert result == {
            "type": "from",
            "column": "Backlog",
            "direction": "forward",
            "negated": True,
        }

    def test_parse_not_been(self):
        result = _parse_single_condition("not:been:Blocked")
        assert result == {"type": "been", "column": "Blocked", "negated": True}

    def test_parse_not_backward(self):
        result = _parse_single_condition("not:backward")
        assert result == {"type": "backward", "negated": True}

    def test_parse_not_forward(self):
        result = _parse_single_condition("not:forward")
        assert result == {"type": "forward", "negated": True}


class TestParseColumnPatterns:
    def test_single_simple_pattern(self):
        patterns = parse_column_patterns("backward")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 1
        assert patterns[0].conditions[0]["type"] == "backward"

    def test_single_from_pattern(self):
        patterns = parse_column_patterns("from:In Progress:forward")
        assert len(patterns) == 1
        assert patterns[0].conditions[0] == {
            "type": "from",
            "column": "In Progress",
            "direction": "forward",
        }

    def test_or_patterns_with_comma(self):
        patterns = parse_column_patterns("backward,to:Done")
        assert len(patterns) == 2
        assert patterns[0].conditions[0]["type"] == "backward"
        assert patterns[1].conditions[0] == {"type": "to", "column": "Done"}

    def test_and_patterns_with_plus(self):
        patterns = parse_column_patterns("from:In Progress+in:Done")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 2
        assert patterns[0].conditions[0] == {"type": "from", "column": "In Progress"}
        assert patterns[0].conditions[1] == {"type": "in", "column": "Done"}

    def test_complex_or_and_combination(self):
        patterns = parse_column_patterns("from:A:forward+in:B,to:C")
        assert len(patterns) == 2
        # First pattern: from:A:forward AND in:B
        assert len(patterns[0].conditions) == 2
        # Second pattern: to:C
        assert len(patterns[1].conditions) == 1

    def test_empty_pattern_error(self):
        with pytest.raises(PhabfiveException) as exc:
            parse_column_patterns("")
        assert "Empty transition pattern" in str(exc.value)

    def test_whitespace_handling(self):
        patterns = parse_column_patterns(" from:A , to:B ")
        assert len(patterns) == 2


class TestColumnPatternMatching:
    def test_matches_backward(self):
        pattern = ColumnPattern([{"type": "backward"}])

        # Transaction with backward movement (sequence 2 -> 1)
        transactions = [
            {
                "oldValue": ["board-phid", "col1-phid"],
                "newValue": ["board-phid", "col2-phid"],
            }
        ]
        column_info = {
            "col1-phid": {"name": "In Progress", "sequence": 2},
            "col2-phid": {"name": "Backlog", "sequence": 1},
        }

        assert pattern.matches(transactions, None, column_info) is True

    def test_matches_forward(self):
        pattern = ColumnPattern([{"type": "forward"}])

        # Transaction with forward movement (sequence 1 -> 2)
        transactions = [
            {
                "oldValue": ["board-phid", "col1-phid"],
                "newValue": ["board-phid", "col2-phid"],
            }
        ]
        column_info = {
            "col1-phid": {"name": "Backlog", "sequence": 1},
            "col2-phid": {"name": "In Progress", "sequence": 2},
        }

        assert pattern.matches(transactions, None, column_info) is True

    def test_matches_from_forward_direction(self):
        pattern = ColumnPattern(
            [{"type": "from", "column": "Backlog", "direction": "forward"}]
        )

        transactions = [
            {
                "oldValue": ["board-phid", "col1-phid"],
                "newValue": ["board-phid", "col2-phid"],
            }
        ]
        column_info = {
            "col1-phid": {"name": "Backlog", "sequence": 1},
            "col2-phid": {"name": "In Progress", "sequence": 2},
        }

        assert pattern.matches(transactions, None, column_info) is True

    def test_matches_in(self):
        pattern = ColumnPattern([{"type": "in", "column": "Blocked"}])

        assert pattern.matches([], "Blocked", {}) is True
        assert pattern.matches([], "Done", {}) is False

    def test_matches_to(self):
        pattern = ColumnPattern([{"type": "to", "column": "Done"}])

        transactions = [
            {
                "oldValue": ["board-phid", "col1-phid"],
                "newValue": ["board-phid", "col2-phid"],
            }
        ]
        column_info = {
            "col1-phid": {"name": "In Progress", "sequence": 2},
            "col2-phid": {"name": "Done", "sequence": 3},
        }

        assert pattern.matches(transactions, None, column_info) is True

    def test_matches_been(self):
        pattern = ColumnPattern([{"type": "been", "column": "Blocked"}])

        transactions = [
            {
                "oldValue": ["board-phid", "blocked-phid"],
                "newValue": ["board-phid", "done-phid"],
            }
        ]
        column_info = {
            "blocked-phid": {"name": "Blocked", "sequence": 1},
            "done-phid": {"name": "Done", "sequence": 2},
        }

        assert pattern.matches(transactions, None, column_info) is True

    def test_matches_never(self):
        pattern = ColumnPattern([{"type": "never", "column": "Blocked"}])

        transactions = [
            {
                "oldValue": ["board-phid", "col1-phid"],
                "newValue": ["board-phid", "col2-phid"],
            }
        ]
        column_info = {
            "col1-phid": {"name": "Backlog", "sequence": 1},
            "col2-phid": {"name": "Done", "sequence": 2},
        }

        assert pattern.matches(transactions, None, column_info) is True

    def test_matches_and_conditions(self):
        # Pattern with AND: from:A AND in:B
        pattern = ColumnPattern(
            [
                {"type": "from", "column": "In Progress"},
                {"type": "in", "column": "Done"},
            ]
        )

        transactions = [
            {
                "oldValue": ["board-phid", "inprogress-phid"],
                "newValue": ["board-phid", "done-phid"],
            }
        ]
        column_info = {
            "inprogress-phid": {"name": "In Progress", "sequence": 2},
            "done-phid": {"name": "Done", "sequence": 3},
        }

        # Both conditions must match
        assert pattern.matches(transactions, "Done", column_info) is True
        assert pattern.matches(transactions, "Blocked", column_info) is False

    def test_no_match(self):
        pattern = ColumnPattern([{"type": "to", "column": "Done"}])

        transactions = [
            {
                "oldValue": ["board-phid", "col1-phid"],
                "newValue": ["board-phid", "col2-phid"],
            }
        ]
        column_info = {
            "col1-phid": {"name": "Backlog", "sequence": 1},
            "col2-phid": {"name": "In Progress", "sequence": 2},
        }

        assert pattern.matches(transactions, None, column_info) is False


class TestColumnFilteringIntegration:
    """Integration tests for the full column filtering workflow."""

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_full_filtering_workflow(self, mock_init):
        """Test the complete flow: search -> fetch transitions -> filter -> display."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"

        # Mock task search results
        maniphest.phab.maniphest.search.side_effect = [
            # First call: initial search
            MagicMock(
                response={
                    "data": [
                        {
                            "id": 1,
                            "phid": "PHID-TASK-1",
                            "fields": {
                                "name": "Test Task 1",
                                "status": {"name": "Open"},
                                "priority": {"name": "Normal"},
                                "description": {"raw": "Description 1"},
                            },
                            "attachments": {
                                "columns": {
                                    "boards": {
                                        "PHID-PROJ-123": {
                                            "columns": [
                                                {
                                                    "phid": "PHID-COL-DONE",
                                                    "name": "Done",
                                                }
                                            ]
                                        }
                                    }
                                }
                            },
                        },
                        {
                            "id": 2,
                            "phid": "PHID-TASK-2",
                            "fields": {
                                "name": "Test Task 2",
                                "status": {"name": "Resolved"},
                                "priority": {"name": "High"},
                                "description": {"raw": "Description 2"},
                            },
                            "attachments": {
                                "columns": {
                                    "boards": {
                                        "PHID-PROJ-123": {
                                            "columns": [
                                                {
                                                    "phid": "PHID-COL-INPROGRESS",
                                                    "name": "In Progress",
                                                }
                                            ]
                                        }
                                    }
                                }
                            },
                        },
                    ]
                }
            ),
            # Subsequent calls for _fetch_task_transactions
            {"data": [{"id": 1}]},
            {"data": [{"id": 2}]},
        ]

        # Mock gettasktransactions for transition history
        maniphest.phab.maniphest.gettasktransactions.side_effect = [
            # Task 1: Backward movement (Done -> In Progress -> Done)
            {
                "1": [
                    {
                        "transactionType": "core:columns",
                        "newValue": [
                            {
                                "boardPHID": "PHID-PROJ-123",
                                "columnPHID": "PHID-COL-DONE",
                                "fromColumnPHIDs": {
                                    "PHID-COL-INPROGRESS": "PHID-COL-INPROGRESS"
                                },
                            }
                        ],
                        "dateCreated": 1234567893,
                    },
                    {
                        "transactionType": "core:columns",
                        "newValue": [
                            {
                                "boardPHID": "PHID-PROJ-123",
                                "columnPHID": "PHID-COL-INPROGRESS",
                                "fromColumnPHIDs": {"PHID-COL-DONE": "PHID-COL-DONE"},
                            }
                        ],
                        "dateCreated": 1234567892,
                    },
                ]
            },
            # Task 2: Only forward movement
            {
                "2": [
                    {
                        "transactionType": "core:columns",
                        "newValue": [
                            {
                                "boardPHID": "PHID-PROJ-123",
                                "columnPHID": "PHID-COL-INPROGRESS",
                                "fromColumnPHIDs": {
                                    "PHID-COL-BACKLOG": "PHID-COL-BACKLOG"
                                },
                            }
                        ],
                        "dateCreated": 1234567890,
                    }
                ]
            },
        ]

        # Mock column info
        maniphest.phab.project.column.search.return_value = {
            "data": [
                {
                    "phid": "PHID-COL-BACKLOG",
                    "fields": {"name": "Backlog", "sequence": 1},
                },
                {
                    "phid": "PHID-COL-INPROGRESS",
                    "fields": {"name": "In Progress", "sequence": 2},
                },
                {"phid": "PHID-COL-DONE", "fields": {"name": "Done", "sequence": 3}},
            ]
        }

        # Parse transition pattern: find tasks with backward movement
        patterns = parse_column_patterns("backward")

        # Verify pattern parsing
        assert len(patterns) == 1
        assert patterns[0].conditions[0]["type"] == "backward"

        # Store task data before it's consumed by the iterator
        task1 = {
            "id": 1,
            "phid": "PHID-TASK-1",
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-123": {
                            "columns": [{"phid": "PHID-COL-DONE", "name": "Done"}]
                        }
                    }
                }
            },
        }
        task2 = {
            "id": 2,
            "phid": "PHID-TASK-2",
            "attachments": {
                "columns": {
                    "boards": {
                        "PHID-PROJ-123": {
                            "columns": [
                                {"phid": "PHID-COL-INPROGRESS", "name": "In Progress"}
                            ]
                        }
                    }
                }
            },
        }

        # Reset side effects for transaction fetching in the actual test
        maniphest.phab.maniphest.search.side_effect = [
            {"data": [{"id": 1}]},
            {"data": [{"id": 2}]},
        ]

        matches1, _, _ = maniphest._task_matches_any_pattern(
            task1, "PHID-TASK-1", patterns, ["PHID-PROJ-123"]
        )
        matches2, _, _ = maniphest._task_matches_any_pattern(
            task2, "PHID-TASK-2", patterns, ["PHID-PROJ-123"]
        )

        # Task 1 should match (has backward movement)
        assert matches1 is True

        # Task 2 should not match (only forward movement)
        assert matches2 is False


class TestYAMLOutput:
    """Test that YAML output is properly formatted and parsable."""

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_yaml_output_is_parsable(self, mock_init, capsys):
        """Test that task_search generates valid YAML output."""
        from io import StringIO

        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        # Mock the phab API
        maniphest.phab = MagicMock()

        # Mock project.query to return a project with slugs
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {
                "name": "Test Project",
                "slugs": ["test-project", "test_project"],
            }
        }
        maniphest.phab.project.query.return_value = mock_project_result

        # Mock maniphest search with tasks containing special YAML characters
        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": "Bug: Authentication failed in api.login()",
                        "status": {"name": "Open"},
                        "priority": {"name": "High"},
                        "description": {
                            "raw": "Steps to reproduce:\n1. Call api.login()\n2. Check response"
                        },
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {
                        "columns": {
                            "boards": {
                                "PHID-PROJ-123": {
                                    "columns": [
                                        {
                                            "phid": "PHID-COL-1",
                                            "name": "In Progress: Review",
                                        }
                                    ]
                                }
                            }
                        }
                    },
                },
                {
                    "id": 2,
                    "phid": "PHID-TASK-2",
                    "fields": {
                        "name": "Feature request: Add support for {template} variables",
                        "status": {"name": "Resolved"},
                        "priority": {"name": "Normal"},
                        "description": {"raw": ""},
                        "dateCreated": 1234567800,
                        "dateModified": 1234567850,
                        "dateClosed": 1234567900,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        # Call task_search
        maniphest.task_search(tag="Test Project")

        # Capture output
        captured = capsys.readouterr()
        yaml_output = captured.out

        # Parse the YAML to verify it's valid
        try:
            yaml_parser = YAML()
            parsed_data = yaml_parser.load(StringIO(yaml_output))
        except Exception as e:
            pytest.fail(
                f"Generated YAML is not parsable: {e}\n\nOutput:\n{yaml_output}"
            )

        # Verify the structure
        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 2

        # Check first task (contains colons in name)
        task1 = parsed_data[0]
        assert "Link" in task1
        assert task1["Link"] == "https://phabricator.example.com/T1"
        assert "Task" in task1
        assert task1["Task"]["Name"] == "Bug: Authentication failed in api.login()"
        assert task1["Task"]["Status"] == "Open"
        assert task1["Task"]["Priority"] == "High"

        # Check second task (contains curly braces in name)
        task2 = parsed_data[1]
        assert (
            task2["Task"]["Name"]
            == "Feature request: Add support for {template} variables"
        )
        assert task2["Task"]["Status"] == "Resolved"


class TestYAMLQuoting:
    """Test that special characters are properly quoted in YAML output."""

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_colon(self, mock_init):
        """Test that colons trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting("foo:bar") is True
        assert maniphest._needs_yaml_quoting("http://example.com") is True

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_braces(self, mock_init):
        """Test that curly braces trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting("{foo}") is True
        assert maniphest._needs_yaml_quoting("${variable}") is True
        assert maniphest._needs_yaml_quoting("foo}bar") is True

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_brackets(self, mock_init):
        """Test that square brackets trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting("[BUG]") is True
        assert maniphest._needs_yaml_quoting("[FEATURE] Add something") is True
        assert maniphest._needs_yaml_quoting("foo]bar") is True

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_backticks(self, mock_init):
        """Test that backticks trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting("`code`") is True
        assert maniphest._needs_yaml_quoting("Run `make build`") is True

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_single_quotes(self, mock_init):
        """Test that single quotes trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting("'LOREM'") is True
        assert maniphest._needs_yaml_quoting("It's working") is True
        assert maniphest._needs_yaml_quoting("Don't do that") is True

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_double_quotes(self, mock_init):
        """Test that double quotes trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting('"LOREM"') is True
        assert maniphest._needs_yaml_quoting('Say "hello"') is True

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_empty_string(self, mock_init):
        """Test that empty strings trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting("") is True

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_safe_strings(self, mock_init):
        """Test that safe strings don't trigger quoting."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting("Normal task name") is False
        assert maniphest._needs_yaml_quoting("Task with numbers 123") is False
        assert maniphest._needs_yaml_quoting("Task-with-dashes") is False
        assert maniphest._needs_yaml_quoting("Task_with_underscores") is False

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_needs_yaml_quoting_non_string(self, mock_init):
        """Test that non-strings return False."""
        mock_init.return_value = None
        maniphest = Maniphest()
        assert maniphest._needs_yaml_quoting(123) is False
        assert maniphest._needs_yaml_quoting(None) is False
        assert maniphest._needs_yaml_quoting(["list"]) is False

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_yaml_output_with_brackets(self, mock_init, capsys):
        """Test that task names with square brackets produce valid YAML."""
        from io import StringIO

        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        maniphest.phab = MagicMock()
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {"name": "Test Project", "slugs": []}
        }
        maniphest.phab.project.query.return_value = mock_project_result

        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": "[BUG] Something is broken",
                        "status": {"name": "Open"},
                        "priority": {"name": "High"},
                        "description": {"raw": ""},
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        maniphest.task_search(tag="Test Project")

        captured = capsys.readouterr()
        yaml_output = captured.out

        yaml_parser = YAML()
        parsed_data = yaml_parser.load(StringIO(yaml_output))

        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 1
        assert parsed_data[0]["Task"]["Name"] == "[BUG] Something is broken"

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_yaml_output_with_backticks(self, mock_init, capsys):
        """Test that task names with backticks produce valid YAML."""
        from io import StringIO

        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        maniphest.phab = MagicMock()
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {"name": "Test Project", "slugs": []}
        }
        maniphest.phab.project.query.return_value = mock_project_result

        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": "Fix `make build` command",
                        "status": {"name": "Open"},
                        "priority": {"name": "Normal"},
                        "description": {"raw": "Run `make test` first"},
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        maniphest.task_search(tag="Test Project")

        captured = capsys.readouterr()
        yaml_output = captured.out

        yaml_parser = YAML()
        parsed_data = yaml_parser.load(StringIO(yaml_output))

        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 1
        assert parsed_data[0]["Task"]["Name"] == "Fix `make build` command"
        assert parsed_data[0]["Task"]["Description"] == "Run `make test` first"

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_yaml_output_with_mixed_special_chars(self, mock_init, capsys):
        """Test task with multiple special character types."""
        from io import StringIO

        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        maniphest.phab = MagicMock()
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {"name": "Test Project", "slugs": []}
        }
        maniphest.phab.project.query.return_value = mock_project_result

        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": "[BUG]: Fix {template} rendering in `parser.py`",
                        "status": {"name": "Open"},
                        "priority": {"name": "High"},
                        "description": {"raw": ""},
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        maniphest.task_search(tag="Test Project")

        captured = capsys.readouterr()
        yaml_output = captured.out

        yaml_parser = YAML()
        parsed_data = yaml_parser.load(StringIO(yaml_output))

        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 1
        assert (
            parsed_data[0]["Task"]["Name"]
            == "[BUG]: Fix {template} rendering in `parser.py`"
        )

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_yaml_output_with_single_quotes(self, mock_init, capsys):
        """Test that task names with single quotes produce valid YAML with preserved quotes."""
        from io import StringIO

        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        maniphest.phab = MagicMock()
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {"name": "Test Project", "slugs": []}
        }
        maniphest.phab.project.query.return_value = mock_project_result

        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": "'LOREM'",
                        "status": {"name": "Open"},
                        "priority": {"name": "Normal"},
                        "description": {"raw": ""},
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        maniphest.task_search(tag="Test Project")

        captured = capsys.readouterr()
        yaml_output = captured.out

        yaml_parser = YAML()
        parsed_data = yaml_parser.load(StringIO(yaml_output))

        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 1
        # Single quotes should be preserved in the parsed value
        assert parsed_data[0]["Task"]["Name"] == "'LOREM'"

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_yaml_output_with_double_quotes(self, mock_init, capsys):
        """Test that task names with double quotes produce valid YAML with preserved quotes."""
        from io import StringIO

        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        maniphest.phab = MagicMock()
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {"name": "Test Project", "slugs": []}
        }
        maniphest.phab.project.query.return_value = mock_project_result

        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": '"LOREM"',
                        "status": {"name": "Open"},
                        "priority": {"name": "Normal"},
                        "description": {"raw": ""},
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        maniphest.task_search(tag="Test Project")

        captured = capsys.readouterr()
        yaml_output = captured.out

        yaml_parser = YAML()
        parsed_data = yaml_parser.load(StringIO(yaml_output))

        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 1
        # Double quotes should be preserved in the parsed value
        assert parsed_data[0]["Task"]["Name"] == '"LOREM"'


class TestStrictFormat:
    """Test that strict format produces guaranteed valid YAML via ruamel.yaml."""

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_strict_format_output_is_valid_yaml(self, mock_init, capsys):
        """Test that strict format produces valid YAML."""
        from io import StringIO

        from phabfive.core import Phabfive
        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        # Set strict output format
        Phabfive.set_output_options(output_format="strict")

        maniphest.phab = MagicMock()
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {"name": "Test Project", "slugs": []}
        }
        maniphest.phab.project.query.return_value = mock_project_result

        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": "Simple task",
                        "status": {"name": "Open"},
                        "priority": {"name": "Normal"},
                        "description": {"raw": ""},
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        maniphest.task_search(tag="Test Project")

        captured = capsys.readouterr()
        yaml_output = captured.out

        yaml_parser = YAML()
        parsed_data = yaml_parser.load(StringIO(yaml_output))

        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 1
        assert parsed_data[0]["Task"]["Name"] == "Simple task"

        # Reset to default
        Phabfive.set_output_options(output_format="rich")

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_strict_format_with_special_chars(self, mock_init, capsys):
        """Test that strict format handles special characters correctly."""
        from io import StringIO

        from phabfive.core import Phabfive
        from ruamel.yaml import YAML

        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.url = "https://phabricator.example.com"

        # Set strict output format
        Phabfive.set_output_options(output_format="strict")

        maniphest.phab = MagicMock()
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {"name": "Test Project", "slugs": []}
        }
        maniphest.phab.project.query.return_value = mock_project_result

        mock_response = MagicMock()
        mock_response.response = {
            "data": [
                {
                    "id": 1,
                    "phid": "PHID-TASK-1",
                    "fields": {
                        "name": "[BUG]: Fix {template} `code` 'quotes' \"double\"",
                        "status": {"name": "Open"},
                        "priority": {"name": "Normal"},
                        "description": {"raw": ""},
                        "dateCreated": 1234567890,
                        "dateModified": 1234567900,
                        "dateClosed": None,
                    },
                    "attachments": {"columns": {"boards": {}}},
                },
            ]
        }
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        maniphest.task_search(tag="Test Project")

        captured = capsys.readouterr()
        yaml_output = captured.out

        yaml_parser = YAML()
        parsed_data = yaml_parser.load(StringIO(yaml_output))

        assert isinstance(parsed_data, list)
        assert len(parsed_data) == 1
        # All special characters should be preserved
        assert (
            parsed_data[0]["Task"]["Name"]
            == "[BUG]: Fix {template} `code` 'quotes' \"double\""
        )

        # Reset to default
        Phabfive.set_output_options(output_format="rich")


class TestTaskSearchTextQuery:
    """Test suite for free-text search functionality."""

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_with_text_query_only(self, mock_init, capsys):
        """Test searching with only a free-text query."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"

        # Mock the API response
        mock_response = MagicMock()
        mock_response.response = {"data": []}
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        # Mock project.query for board name resolution
        maniphest.phab.project.query.return_value = {"data": {}}

        # Call with text query only
        maniphest.task_search(text_query="authentication bug")

        # Verify API was called with query constraint only
        assert maniphest.phab.maniphest.search.called
        call_kwargs = maniphest.phab.maniphest.search.call_args[1]
        constraints = call_kwargs["constraints"]

        # Should have query constraint
        assert "query" in constraints
        assert constraints["query"] == "authentication bug"

        # Should NOT have projects constraint
        assert "projects" not in constraints

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_with_tag_only(self, mock_init, capsys):
        """Test searching with only --tag option (backward compat)."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"

        # Mock project resolution
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {
                "name": "MyProject",
                "slugs": ["myproject"],
            }
        }
        maniphest.phab.project.query.return_value = mock_project_result

        # Mock the API response
        mock_response = MagicMock()
        mock_response.response = {"data": []}
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        # Call with tag only
        maniphest.task_search(tag="MyProject")

        # Verify API was called with projects constraint only
        assert maniphest.phab.maniphest.search.called
        call_kwargs = maniphest.phab.maniphest.search.call_args[1]
        constraints = call_kwargs["constraints"]

        # Should have projects constraint
        assert "projects" in constraints
        assert constraints["projects"] == ["PHID-PROJ-123"]

        # Should NOT have query constraint
        assert "query" not in constraints

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_with_text_and_tag(self, mock_init, capsys):
        """Test searching with both text query and tag filter."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"

        # Mock project resolution
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-123": {
                "name": "MyProject",
                "slugs": ["myproject"],
            }
        }
        maniphest.phab.project.query.return_value = mock_project_result

        # Mock the API response
        mock_response = MagicMock()
        mock_response.response = {"data": []}
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        # Call with both text query and tag
        maniphest.task_search(text_query="bug", tag="MyProject")

        # Verify API was called with both constraints
        assert maniphest.phab.maniphest.search.called
        call_kwargs = maniphest.phab.maniphest.search.call_args[1]
        constraints = call_kwargs["constraints"]

        # Should have both constraints
        assert "query" in constraints
        assert constraints["query"] == "bug"
        assert "projects" in constraints
        assert constraints["projects"] == ["PHID-PROJ-123"]

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_requires_at_least_one_filter(self, mock_init, capsys):
        """Test that search without any filters raises an exception."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()

        # Call with no filters - should raise exception
        with pytest.raises(PhabfiveConfigException) as exc_info:
            maniphest.task_search()

        # Verify the error message contains helpful information
        assert "No search criteria specified" in str(exc_info.value)

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_with_text_and_date_filters(self, mock_init, capsys):
        """Test text search with date filters."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"

        # Mock the API response
        mock_response = MagicMock()
        mock_response.response = {"data": []}
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        # Mock project.query for board name resolution
        maniphest.phab.project.query.return_value = {"data": {}}

        # Call with text query and date filters
        maniphest.task_search(text_query="bug", created_after=7, updated_after=3)

        # Verify API was called with all constraints
        assert maniphest.phab.maniphest.search.called
        call_kwargs = maniphest.phab.maniphest.search.call_args[1]
        constraints = call_kwargs["constraints"]

        # Should have query constraint
        assert "query" in constraints
        assert constraints["query"] == "bug"

        # Should have date constraints (converted to Unix timestamps)
        assert "createdStart" in constraints
        assert "modifiedStart" in constraints
        assert isinstance(constraints["createdStart"], int)
        assert isinstance(constraints["modifiedStart"], int)

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_tag_supports_wildcards(self, mock_init, capsys):
        """Test that --tag option supports wildcard patterns."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"

        # Mock project resolution with wildcard match
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-1": {
                "name": "Development",
                "slugs": ["development", "dev"],
            },
            "PHID-PROJ-2": {
                "name": "DevOps",
                "slugs": ["devops"],
            },
        }
        maniphest.phab.project.query.return_value = mock_project_result

        # Mock the API response
        mock_response = MagicMock()
        mock_response.response = {"data": []}
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        # Call with wildcard pattern
        maniphest.task_search(tag="dev*")

        # Verify API was called multiple times (once per matched project)
        assert maniphest.phab.maniphest.search.called
        # With 2 matching projects, should make 2 calls
        assert maniphest.phab.maniphest.search.call_count >= 2

    @patch("phabfive.maniphest.Phabfive.__init__")
    def test_task_search_tag_supports_and_or_logic(self, mock_init, capsys):
        """Test that --tag option supports AND/OR pattern logic."""
        mock_init.return_value = None
        maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "https://phabricator.example.com"

        # Mock project resolution
        mock_project_result = MagicMock()
        mock_project_result.get.return_value = {
            "PHID-PROJ-A": {
                "name": "ProjectA",
                "slugs": ["projecta"],
            },
            "PHID-PROJ-B": {
                "name": "ProjectB",
                "slugs": ["projectb"],
            },
        }
        maniphest.phab.project.query.return_value = mock_project_result

        # Mock the API response
        mock_response = MagicMock()
        mock_response.response = {"data": []}
        mock_response.get.return_value = {"after": None}
        maniphest.phab.maniphest.search.return_value = mock_response

        # Test OR logic: ProjectA,ProjectB
        maniphest.task_search(tag="ProjectA,ProjectB")

        # Verify API was called (implementation fetches both projects)
        assert maniphest.phab.maniphest.search.called
        # Should make multiple calls for OR logic
        assert maniphest.phab.maniphest.search.call_count >= 2


class TestParseTimeWithUnit:
    """Test the parse_time_with_unit function."""

    def test_parse_none(self):
        """Test that None input returns None."""
        assert parse_time_with_unit(None) is None

    def test_parse_plain_integer(self):
        """Test parsing plain integer (backward compatibility)."""
        assert parse_time_with_unit(7) == 7.0
        assert parse_time_with_unit(1) == 1.0
        assert parse_time_with_unit(30) == 30.0

    def test_parse_plain_string_number(self):
        """Test parsing plain string number (backward compatibility)."""
        assert parse_time_with_unit("7") == 7.0
        assert parse_time_with_unit("1") == 1.0
        assert parse_time_with_unit("30") == 30.0

    def test_parse_hours(self):
        """Test parsing hours."""
        # 1 hour = 1/24 days
        assert parse_time_with_unit("1h") == pytest.approx(1 / 24)
        # 24 hours = 1 day
        assert parse_time_with_unit("24h") == pytest.approx(1.0)
        # 12 hours = 0.5 days
        assert parse_time_with_unit("12h") == pytest.approx(0.5)

    def test_parse_days_with_unit(self):
        """Test parsing days with explicit 'd' unit."""
        assert parse_time_with_unit("1d") == 1.0
        assert parse_time_with_unit("7d") == 7.0
        assert parse_time_with_unit("30d") == 30.0

    def test_parse_weeks(self):
        """Test parsing weeks."""
        assert parse_time_with_unit("1w") == 7.0
        assert parse_time_with_unit("2w") == 14.0
        assert parse_time_with_unit("4w") == 28.0

    def test_parse_months(self):
        """Test parsing months (30 days)."""
        assert parse_time_with_unit("1m") == 30.0
        assert parse_time_with_unit("2m") == 60.0
        assert parse_time_with_unit("6m") == 180.0

    def test_parse_years(self):
        """Test parsing years (365 days)."""
        assert parse_time_with_unit("1y") == 365.0
        assert parse_time_with_unit("2y") == 730.0

    def test_parse_float_values(self):
        """Test parsing float values."""
        assert parse_time_with_unit("1.5w") == 10.5
        assert parse_time_with_unit("0.5m") == 15.0
        assert parse_time_with_unit("2.5d") == 2.5

    def test_parse_with_whitespace(self):
        """Test parsing with whitespace between number and unit."""
        assert parse_time_with_unit("1 w") == 7.0
        assert parse_time_with_unit("2  m") == 60.0
        assert parse_time_with_unit("  7d  ") == 7.0

    def test_parse_case_insensitive(self):
        """Test that units are case insensitive."""
        assert parse_time_with_unit("1W") == 7.0
        assert parse_time_with_unit("1M") == 30.0
        assert parse_time_with_unit("1Y") == 365.0
        assert parse_time_with_unit("1D") == 1.0
        assert parse_time_with_unit("1H") == pytest.approx(1 / 24)

    def test_parse_zero(self):
        """Test parsing zero values."""
        assert parse_time_with_unit(0) == 0.0
        assert parse_time_with_unit("0") == 0.0
        assert parse_time_with_unit("0d") == 0.0
        assert parse_time_with_unit("0w") == 0.0

    def test_invalid_empty_string(self):
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="Time value cannot be empty"):
            parse_time_with_unit("")
        with pytest.raises(ValueError, match="Time value cannot be empty"):
            parse_time_with_unit("   ")

    def test_invalid_unit(self):
        """Test that invalid unit raises ValueError."""
        with pytest.raises(ValueError, match="Invalid time unit: 'x'"):
            parse_time_with_unit("5x")
        with pytest.raises(ValueError, match="Invalid time unit: 'days'"):
            parse_time_with_unit("5days")

    def test_invalid_format(self):
        """Test that invalid format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_with_unit("abc")
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_with_unit("w1")
        with pytest.raises(ValueError, match="Invalid time format"):
            parse_time_with_unit("1.2.3d")

    def test_negative_value(self):
        """Test that negative values raise ValueError."""
        with pytest.raises(ValueError, match="Time value cannot be negative"):
            parse_time_with_unit("-1d")
        with pytest.raises(ValueError, match="Time value cannot be negative"):
            parse_time_with_unit("-5w")

    def test_examples_from_issue(self):
        """Test examples from GitHub issue #105."""
        # The issue specifically mentions --updated-after=1w
        assert parse_time_with_unit("1w") == 7.0
