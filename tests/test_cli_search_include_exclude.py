# -*- coding: utf-8 -*-
"""Tests for the --include/--exclude options of maniphest search."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app

runner = CliRunner()


def _output(result):
    """Combined stdout/stderr regardless of click version."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass
    return output


class TestSearchIncludeExcludeCli:
    def _invoke(self, args):
        mock_m = MagicMock()
        mock_m.task_search.return_value = None
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, ["search", *args])
        return result, mock_m

    def test_include_comma_separated(self):
        result, mock_m = self._invoke(["--include", "T2069,T2257"])

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["include_task_ids"] == [2069, 2257]
        assert kwargs["exclude_task_ids"] is None

    def test_include_alone_passes_criteria_guard(self):
        result, mock_m = self._invoke(["--include", "T1"])

        assert result.exit_code == 0
        assert "Usage:" not in _output(result)
        mock_m.task_search.assert_called_once()

    def test_exclude_alone_fails_criteria_guard(self):
        result, mock_m = self._invoke(["--exclude", "T1"])

        assert result.exit_code == 0
        assert "Usage:" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_exclude_with_filter(self):
        result, mock_m = self._invoke(["--tag", "MyProject", "--exclude", "T1,T2"])

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["exclude_task_ids"] == [1, 2]
        assert kwargs["include_task_ids"] is None

    def test_invalid_include_id(self):
        result, mock_m = self._invoke(["--include", "BAD"])

        assert result.exit_code == 1
        assert "Invalid task ID 'BAD'" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_invalid_exclude_id(self):
        result, mock_m = self._invoke(["--tag", "MyProject", "--exclude", "T1,K2"])

        assert result.exit_code == 1
        assert "Invalid task ID 'K2'" in _output(result)
        mock_m.task_search.assert_not_called()

    def test_include_exclude_overlap_errors(self):
        result, mock_m = self._invoke(["--include", "T1,T2", "--exclude", "T2"])

        assert result.exit_code == 1
        assert "T2 cannot be both included and excluded" in _output(result)
        mock_m.task_search.assert_not_called()


class TestSearchIncludeExcludeYamlTemplate:
    """YAML templates accept include/exclude as a string or an actual list."""

    def _invoke_with_template(self, search_params):
        mock_m = MagicMock()
        mock_m.task_search.return_value = None
        mock_m._load_search_config.return_value = [
            {
                "search": search_params,
                "title": "Command Line Search",
                "description": None,
            }
        ]
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, ["search", "--with", "template.yaml"])
        return result, mock_m

    def test_include_as_yaml_list(self):
        result, mock_m = self._invoke_with_template({"include": ["T2069", "T2257"]})

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["include_task_ids"] == [2069, 2257]

    def test_include_as_string(self):
        result, mock_m = self._invoke_with_template({"include": "T2069,T2257"})

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["include_task_ids"] == [2069, 2257]

    def test_exclude_as_yaml_list(self):
        result, mock_m = self._invoke_with_template(
            {"include": ["T1"], "exclude": ["T2", "T3"]}
        )

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["include_task_ids"] == [1]
        assert kwargs["exclude_task_ids"] == [2, 3]

    def test_list_with_comma_separated_entry(self):
        result, mock_m = self._invoke_with_template({"include": ["T1,T2", "T3"]})

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["include_task_ids"] == [1, 2, 3]

    def test_list_with_invalid_entry_errors(self):
        result, mock_m = self._invoke_with_template({"include": ["T1", "BAD"]})

        assert result.exit_code == 1
        assert "Invalid task ID 'BAD'" in _output(result)
        mock_m.task_search.assert_not_called()
