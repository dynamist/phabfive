# -*- coding: utf-8 -*-

# 3rd party imports
import pytest
from unittest.mock import MagicMock, patch

# phabfive imports
from phabfive.maniphest import (
    Maniphest,
    _extract_variable_dependencies,
    _build_dependency_graph,
    _detect_circular_dependencies,
    _topological_sort,
    _render_variables_with_dependency_resolution,
)
from phabfive.maniphest_transitions import (
    TransitionPattern,
    _parse_single_condition,
    parse_transition_patterns,
)
from phabfive.exceptions import PhabfiveDataException, PhabfiveException


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
        assert "Invalid transition condition type" in str(exc.value)

    def test_missing_colon(self):
        with pytest.raises(PhabfiveException) as exc:
            _parse_single_condition("notavalidpattern")
        assert "Invalid transition condition syntax" in str(exc.value)

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


class TestParseTransitionPatterns:
    def test_single_simple_pattern(self):
        patterns = parse_transition_patterns("backward")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 1
        assert patterns[0].conditions[0]["type"] == "backward"

    def test_single_from_pattern(self):
        patterns = parse_transition_patterns("from:In Progress:forward")
        assert len(patterns) == 1
        assert patterns[0].conditions[0] == {
            "type": "from",
            "column": "In Progress",
            "direction": "forward",
        }

    def test_or_patterns_with_comma(self):
        patterns = parse_transition_patterns("backward,to:Done")
        assert len(patterns) == 2
        assert patterns[0].conditions[0]["type"] == "backward"
        assert patterns[1].conditions[0] == {"type": "to", "column": "Done"}

    def test_and_patterns_with_plus(self):
        patterns = parse_transition_patterns("from:In Progress+in:Done")
        assert len(patterns) == 1
        assert len(patterns[0].conditions) == 2
        assert patterns[0].conditions[0] == {"type": "from", "column": "In Progress"}
        assert patterns[0].conditions[1] == {"type": "in", "column": "Done"}

    def test_complex_or_and_combination(self):
        patterns = parse_transition_patterns("from:A:forward+in:B,to:C")
        assert len(patterns) == 2
        # First pattern: from:A:forward AND in:B
        assert len(patterns[0].conditions) == 2
        # Second pattern: to:C
        assert len(patterns[1].conditions) == 1

    def test_empty_pattern_error(self):
        with pytest.raises(PhabfiveException) as exc:
            parse_transition_patterns("")
        assert "Empty transition pattern" in str(exc.value)

    def test_whitespace_handling(self):
        patterns = parse_transition_patterns(" from:A , to:B ")
        assert len(patterns) == 2


class TestTransitionPatternMatching:
    def test_matches_backward(self):
        pattern = TransitionPattern([{"type": "backward"}])

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
        pattern = TransitionPattern([{"type": "forward"}])

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
        pattern = TransitionPattern(
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
        pattern = TransitionPattern([{"type": "in", "column": "Blocked"}])

        assert pattern.matches([], "Blocked", {}) is True
        assert pattern.matches([], "Done", {}) is False

    def test_matches_to(self):
        pattern = TransitionPattern([{"type": "to", "column": "Done"}])

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
        pattern = TransitionPattern([{"type": "been", "column": "Blocked"}])

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
        pattern = TransitionPattern([{"type": "never", "column": "Blocked"}])

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
        pattern = TransitionPattern(
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
        pattern = TransitionPattern([{"type": "to", "column": "Done"}])

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


class TestTransitionFilteringIntegration:
    """Integration tests for the full transition filtering workflow."""

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
        patterns = parse_transition_patterns("backward")

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
        from ruamel.yaml import YAML
        from io import StringIO

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
        maniphest.task_search(project="Test Project")

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
