# -*- coding: utf-8 -*-

"""What a creation template actually does, where the documentation said otherwise (#466).

Two behaviours were described in `docs/create-templates.md` and `phabfive/SKILL.md`
without existing in the code. The documents now say what happens; these tests pin it,
so the phase that implements the described behaviour instead (#463, from #480 on) has
to flip an assertion on purpose rather than discover it.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import PhabfiveDataException
from phabfive.maniphest.core import Maniphest

PROJECTS = {"PHID-PROJ-backend": {"name": "Backend Team", "slugs": ["backend_team"]}}


def _phab():
    """A client that knows one project and hands out task IDs as it is asked."""
    phab = MagicMock()
    phab.project.query.return_value = {"data": PROJECTS}

    ids = iter(range(1, 100))

    def edit(transactions):
        task_id = next(ids)
        return {"object": {"id": task_id, "phid": f"PHID-TASK-{task_id}"}}

    phab.maniphest.edit.side_effect = edit
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


def _transactions_by_title(phab):
    """Every created task's transactions, keyed by the title it was created with."""
    created = {}

    for call in phab.maniphest.edit.call_args_list:
        transactions = call.kwargs["transactions"]
        title = next(t["value"] for t in transactions if t["type"] == "title")
        created[title] = transactions

    return created


class TestSubtaskProjects:
    """A subtask names its own projects; nothing is inherited from the parent."""

    def test_a_subtask_without_projects_gets_none(self):
        phab = _phab()

        _maniphest(phab).create_tasks_from_config(
            {
                "variables": {},
                "tasks": [
                    {
                        "title": "Epic",
                        "description": "x",
                        "projects": ["Backend Team"],
                        "tasks": [{"title": "Subtask", "description": "x"}],
                    }
                ],
            }
        )

        created = _transactions_by_title(phab)

        assert {
            "type": "projects.add",
            "value": ["PHID-PROJ-backend"],
        } in created["Epic"]
        assert all(t["type"] != "projects.add" for t in created["Subtask"])

    def test_a_subtask_naming_projects_gets_them(self):
        phab = _phab()

        _maniphest(phab).create_tasks_from_config(
            {
                "variables": {},
                "tasks": [
                    {
                        "title": "Epic",
                        "description": "x",
                        "tasks": [
                            {
                                "title": "Subtask",
                                "description": "x",
                                "projects": ["Backend Team"],
                            }
                        ],
                    }
                ],
            }
        )

        created = _transactions_by_title(phab)

        assert {
            "type": "projects.add",
            "value": ["PHID-PROJ-backend"],
        } in created["Subtask"]


class TestSingleDocument:
    """A creation template is one document, where a search template may be several."""

    def test_a_second_document_creates_nothing(self, tmp_path):
        template = tmp_path / "two-documents.yaml"
        template.write_text(
            "variables: {}\n"
            "tasks:\n"
            '  - title: "First"\n'
            '    description: "x"\n'
            "---\n"
            "tasks:\n"
            '  - title: "Second"\n'
            '    description: "x"\n'
        )
        phab = _phab()

        # ruamel's own ComposerError would reach the user as a traceback:
        # the command answers only the phabfive exceptions
        with pytest.raises(PhabfiveDataException) as excinfo:
            _maniphest(phab).create_tasks_from_yaml(str(template))

        assert "two-documents.yaml" in str(excinfo.value)

        # Not even the first document's tasks: the whole file is refused
        phab.maniphest.edit.assert_not_called()
