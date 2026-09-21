# -*- coding: utf-8 -*-
"""Tests for the --visible-to and --editable-by filters of maniphest search (#420)."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest import Maniphest

runner = CliRunner()

HUMANS = "PHID-PROJ-humans"


def _task(task_id, view="users", edit="users"):
    return {
        "id": task_id,
        "phid": f"PHID-TASK-{task_id}",
        "fields": {
            "name": f"Task {task_id}",
            "status": {"value": "open", "name": "Open"},
            "priority": {"name": "Normal", "value": 50},
            "policy": {"view": view, "interact": view, "edit": edit},
        },
        "attachments": {"columns": {"boards": {}}},
    }


TASKS = [
    _task(1, edit="users"),
    _task(2, edit=HUMANS),
    _task(3, edit="users"),
    _task(4, view="PHID-PROJ-secret", edit="PHID-PROJ-secret"),
    _task(5, edit="obj.maniphest.author"),
    _task(6, edit="users"),
]


def _maniphest(tasks=TASKS):
    maniphest = Maniphest.__new__(Maniphest)
    maniphest.phab = MagicMock()
    maniphest.url = "https://phabricator.example.com"
    maniphest.conf = {"PHAB_SPACE": "S1"}
    maniphest.phab.phid.lookup.return_value = {
        "S1": {"phid": "PHID-SPCE-1", "name": "Global", "fullName": "Global"}
    }
    maniphest.phab.maniphest.querystatuses.return_value = {
        "openStatuses": ["open"],
        "closedStatuses": {"1": "resolved"},
        "statusMap": {"open": "Open", "resolved": "Resolved"},
    }
    maniphest.phab.project.search.return_value = {"data": [{"phid": HUMANS}]}
    page = MagicMock(response={"data": tasks})
    page.get.return_value = {"after": None}
    maniphest.phab.maniphest.search.return_value = page
    return maniphest


def _ids(maniphest, **kwargs):
    """The IDs task_search kept, sorted: the order is priority's, not the filter's."""
    with patch.object(Maniphest, "_build_task_display_data") as build:
        maniphest.task_search(**kwargs)
    return sorted(task["id"] for task in build.call_args[0][0])


class TestPolicyFilter:
    def test_editable_by_keyword_matches_the_stored_value(self):
        assert _ids(_maniphest(), editable_by="users") == [1, 3, 6]

    def test_editable_by_project_is_resolved_and_matched_by_phid(self):
        maniphest = _maniphest()

        assert _ids(maniphest, editable_by="#humans") == [2]
        maniphest.phab.project.search.assert_called_once_with(
            constraints={"slugs": ["humans"]}
        )

    def test_a_phid_is_matched_without_a_lookup(self):
        maniphest = _maniphest()

        assert _ids(maniphest, editable_by="PHID-PROJ-secret") == [4]
        maniphest.phab.project.search.assert_not_called()

    def test_visible_to(self):
        assert _ids(_maniphest(), visible_to="PHID-PROJ-secret") == [4]

    def test_both_filters_and(self):
        assert _ids(_maniphest(), visible_to="users", editable_by=HUMANS) == [2]

    def test_no_match_is_empty_not_an_error(self):
        assert _ids(_maniphest(), editable_by="admin") == []

    def test_the_filter_runs_before_the_limit(self):
        # Tasks 1, 3 and 6 match and sort newest first (6, 5, 4, ...). A limit
        # applied first would keep 6 and 5 and then filter down to just 6
        assert _ids(_maniphest(), editable_by="users", limit=2) == [3, 6]

    def test_the_filter_alone_passes_the_criteria_check(self):
        assert _ids(_maniphest(), editable_by="users") == [1, 3, 6]

    def test_a_task_without_a_policy_field_does_not_match(self):
        bare = _task(9)
        del bare["fields"]["policy"]

        assert _ids(_maniphest([bare]), editable_by="users") == []

    def test_a_value_outside_the_grammar_fails_before_the_search(self):
        maniphest = _maniphest()

        with pytest.raises(PhabfiveConfigException, match="--editable-by must be"):
            maniphest.task_search(editable_by="nonsense")
        maniphest.phab.maniphest.search.assert_not_called()

    def test_an_unknown_project_fails_before_the_search(self):
        maniphest = _maniphest()
        maniphest.phab.project.search.return_value = {"data": []}

        with pytest.raises(PhabfiveDataException, match="does not exist"):
            maniphest.task_search(editable_by="#nope")
        maniphest.phab.maniphest.search.assert_not_called()


class TestPolicyFilterCli:
    def _invoke(self, args, template=None):
        mock_m = MagicMock()
        mock_m.task_search.return_value = None
        argv = ["search", *args]
        if template is not None:
            mock_m._load_search_config.return_value = [
                {"search": template, "title": None, "description": None}
            ]
            argv += ["--with", "template.yaml"]
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, argv)
        return result, mock_m

    def test_options_are_passed_through(self):
        result, mock_m = self._invoke(
            ["--visible-to", "users", "--editable-by", "#humans"]
        )

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["visible_to"] == "users"
        assert kwargs["editable_by"] == "#humans"

    @pytest.mark.parametrize("option", ["--visible-to", "--editable-by"])
    def test_a_policy_filter_alone_is_a_criterion(self, option):
        result, mock_m = self._invoke([option, "users"])

        assert "Usage:" not in result.output
        mock_m.task_search.assert_called_once()

    def test_template_keys(self):
        result, mock_m = self._invoke(
            [], template={"visible-to": "users", "editable-by": "admin"}
        )

        assert result.exit_code == 0
        kwargs = mock_m.task_search.call_args[1]
        assert kwargs["visible_to"] == "users"
        assert kwargs["editable_by"] == "admin"

    def test_the_option_beats_the_template(self):
        _, mock_m = self._invoke(
            ["--editable-by", "#humans"], template={"editable-by": "admin"}
        )

        assert mock_m.task_search.call_args[1]["editable_by"] == "#humans"

    @pytest.mark.parametrize(
        "error",
        [
            PhabfiveConfigException("--editable-by must be one of: ..."),
            PhabfiveDataException("Project '#nope' does not exist"),
        ],
    )
    def test_a_bad_value_is_an_error_not_a_traceback(self, error):
        mock_m = MagicMock()
        mock_m.task_search.side_effect = error
        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=mock_m):
            result = runner.invoke(maniphest_app, ["search", "--editable-by", "x"])

        assert result.exit_code == 1
        assert f"ERROR: {error}" in result.output
        assert not isinstance(result.exception, type(error))
