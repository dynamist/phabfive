"""Which projects a task is in, as a multi-tag `maniphest search` reads it.

`--tag a,b` and `--tag a+b` search each project and then keep the tasks that
belong where the pattern says. Membership used to be read off the columns
attachment's `boards`, which lists only projects that have a workboard, so
every task of a board-less project was dropped: two tags that returned tasks
on their own returned nothing together (#519). It is read off the projects
attachment now, which lists every project a task is tagged with.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.filters import task_matches_project_patterns
from phabfive.project_filters import parse_project_patterns

PROJECT_A = "PHID-PROJ-aaaaaaaaaaaaaaaaaaaa"
PROJECT_B = "PHID-PROJ-bbbbbbbbbbbbbbbbbbbb"
PHIDS = {"a": PROJECT_A, "b": PROJECT_B}


def _task(task_id, projects):
    """A task tagged with `projects`, none of which has a workboard."""
    return {
        "id": task_id,
        "phid": f"PHID-TASK-{task_id}",
        "fields": {
            "name": f"Task {task_id}",
            "status": {"name": "Open"},
            "priority": {"name": "Normal", "value": 50},
            "description": {"raw": ""},
            "dateCreated": task_id,
            "dateModified": 0,
            "dateClosed": None,
        },
        "attachments": {
            "columns": {"boards": []},
            "projects": {"projectPHIDs": list(projects)},
        },
    }


def _maniphest(tasks):
    """A Maniphest whose `maniphest.search` answers from `tasks`."""
    maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "https://phorge.example.com"
    maniphest.conf = {}
    maniphest._resolve_project_phids = MagicMock(side_effect=lambda n: [PHIDS[n]])
    maniphest._get_open_statuses = MagicMock(return_value=["open"])

    def search(**kwargs):
        wanted = set(kwargs.get("constraints", {}).get("projects") or [])
        response = MagicMock()
        response.response = {
            "data": [
                task
                for task in tasks
                if wanted <= set(task["attachments"]["projects"]["projectPHIDs"])
            ]
        }
        response.get.return_value = {"after": None}
        return response

    maniphest.phab.maniphest.search.side_effect = search
    return maniphest


def _ids(result):
    return sorted(int(task["_url"].rsplit("/T", 1)[1]) for task in result["tasks"])


TASKS = [_task(1, [PROJECT_A]), _task(2, [PROJECT_B]), _task(3, [PROJECT_A, PROJECT_B])]


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestBoardlessProjects:
    def test_either_tag_keeps_the_tasks_of_both(self, _init):
        result = _maniphest(TASKS).task_search(tag="a,b")

        assert _ids(result) == [1, 2, 3]

    def test_both_tags_keep_the_task_in_both(self, _init):
        result = _maniphest(TASKS).task_search(tag="a+b")

        assert _ids(result) == [3]

    def test_the_search_asks_for_the_projects_attachment(self, _init):
        maniphest = _maniphest(TASKS)

        maniphest.task_search(tag="a,b")

        for call in maniphest.phab.maniphest.search.call_args_list:
            assert call.kwargs["attachments"]["projects"] is True

    def test_a_tag_filter_alone_does_not_warn_about_history(self, _init, caplog):
        """The project filter reads no transactions, so it costs nothing per task."""
        many = [_task(n, [PROJECT_A]) for n in range(1, 60)]

        with caplog.at_level(logging.WARNING):
            result = _maniphest(many).task_search(tag="a,b")

        assert len(result["tasks"]) == 59
        assert "transition history" not in caplog.text


class TestTaskMatchesProjectPatterns:
    @pytest.mark.parametrize(
        "tag, resolved, projects, kept",
        [
            ("a,b", [[PROJECT_A], [PROJECT_B]], [PROJECT_B], True),
            ("a,b", [[PROJECT_A], [PROJECT_B]], [], False),
            ("a+b", [[(PROJECT_A, PROJECT_B)]], [PROJECT_A, PROJECT_B], True),
            ("a+b", [[(PROJECT_A, PROJECT_B)]], [PROJECT_A], False),
        ],
    )
    def test_membership_is_the_projects_attachment(self, tag, resolved, projects, kept):
        task = _task(1, projects)

        assert (
            task_matches_project_patterns(task, parse_project_patterns(tag), resolved)
            is kept
        )

    def test_a_board_is_not_membership(self):
        """A board with no projects attachment behind it is not a tag."""
        task = {"id": 1, "attachments": {"columns": {"boards": {PROJECT_A: {}}}}}

        assert not task_matches_project_patterns(
            task, parse_project_patterns("a,b"), [[PROJECT_A], [PROJECT_B]]
        )
