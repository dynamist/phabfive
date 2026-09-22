# -*- coding: utf-8 -*-

"""Rendering a creation template (#464).

`maniphest create --with` renders every string a template names with Jinja2,
`projects` included - docs/create-templates.md names a project by variable in
five places, and each one used to be looked up with the braces still in it.

`variables` is optional, as the same document says: a template that defines
none, or writes an empty or null mapping, renders what it has and creates its
tasks rather than dying on a KeyError the CLI has no handler for.
"""

import copy
from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import (
    PhabfiveConfigException,
    PhabfiveDataException,
    PhabfiveRemoteException,
)
from phabfive.maniphest.core import Maniphest

PROJECTS = {
    "PHID-PROJ-backend": {"name": "Backend Team", "slugs": ["backend"]},
    "PHID-PROJ-sprint": {"name": "Sprint 42", "slugs": []},
}


def _phab(projects=None):
    """A client that knows the projects in `projects`, and creates any task."""
    phab = MagicMock()
    phab.project.query.return_value = {
        "data": PROJECTS if projects is None else projects
    }

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


def _create(phab, tasks, config=None, dry_run=False):
    """Create `tasks`, with `config` holding whatever else the template says."""
    return _maniphest(phab).create_tasks_from_config(
        {**(config or {}), "tasks": tasks}, dry_run=dry_run
    )


def _task(title="Task", **fields):
    return {"title": title, "description": "x", **fields}


def _transactions(phab, call=0):
    return phab.maniphest.edit.call_args_list[call].kwargs["transactions"]


class TestProjects:
    def test_a_variable_names_a_project(self):
        """docs/create-templates.md writes projects: ["{{ project_name }}"]."""
        phab = _phab()

        _create(
            phab,
            [_task(projects=["{{ project_name }}"])],
            config={"variables": {"project_name": "Backend Team"}},
        )

        assert {
            "type": "projects.set",
            "value": ["PHID-PROJ-backend"],
        } in _transactions(phab)

    def test_a_rendered_name_is_the_one_looked_up(self):
        """The braces used to reach the project map, and nothing matched them."""
        phab = _phab()

        with pytest.raises(PhabfiveRemoteException) as excinfo:
            _create(
                phab,
                [_task(projects=["{{ project_name }}"])],
                config={"variables": {"project_name": "Nosuch"}},
            )

        assert "'Nosuch'" in str(excinfo.value)
        assert "{{" not in str(excinfo.value)

    def test_a_variable_names_a_hashtag(self):
        phab = _phab()

        _create(
            phab,
            [_task(projects=["{{ tag }}"])],
            config={"variables": {"tag": "backend"}},
        )

        assert {
            "type": "projects.set",
            "value": ["PHID-PROJ-backend"],
        } in _transactions(phab)

    def test_a_child_task_renders_its_projects_too(self):
        phab = _phab()

        _create(
            phab,
            [_task("Parent", tasks=[_task("Child", projects=["{{ sprint }}"])])],
            config={"variables": {"sprint": "Sprint 42"}},
        )

        assert {
            "type": "projects.set",
            "value": ["PHID-PROJ-sprint"],
        } in _transactions(phab, call=1)

    def test_a_plain_name_still_works(self):
        phab = _phab()

        _create(phab, [_task(projects=["Backend Team"])])

        assert {
            "type": "projects.set",
            "value": ["PHID-PROJ-backend"],
        } in _transactions(phab)

    def test_a_variable_naming_an_ambiguous_project_is_refused(self):
        """Two milestones named alike; the message has to name the rendered one."""
        phab = _phab(
            {
                "PHID-PROJ-one": {"name": "Sprint 1", "slugs": []},
                "PHID-PROJ-two": {"name": "Sprint 1", "slugs": []},
            }
        )
        phab.project.search.return_value = {"data": []}

        with pytest.raises(PhabfiveConfigException) as excinfo:
            _create(
                phab,
                [_task(projects=["{{ sprint }}"])],
                config={"variables": {"sprint": "Sprint 1"}},
            )

        assert "Sprint 1" in str(excinfo.value)
        phab.maniphest.edit.assert_not_called()

    def test_a_null_projects_key_is_no_projects(self):
        """`projects:` with nothing after it used to be a TypeError."""
        phab = _phab()

        result = _create(phab, [_task(projects=None)])

        assert result["task_ids"] == [1]
        assert all(t["type"] != "projects.set" for t in _transactions(phab))

    @pytest.mark.parametrize(
        "projects",
        ["Backend Team", {"Backend Team": None}],
        ids=["string", "mapping"],
    )
    def test_projects_written_as_anything_but_a_list_is_refused(self, projects):
        """A bare string used to be looked up one letter at a time."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="projects takes a list"):
            _create(phab, [_task(projects=projects)])

        phab.maniphest.edit.assert_not_called()

    @pytest.mark.parametrize("item", [None, 1234], ids=["null", "number"])
    def test_an_item_that_is_not_a_project_name_is_refused(self, item):
        """A stray `-` in YAML is a null item, and a bare 1234 is a number."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="projects takes"):
            _create(phab, [_task(projects=["Backend Team", item])])

        phab.maniphest.edit.assert_not_called()


class TestOptionalVariables:
    def test_a_template_without_variables_creates_its_tasks(self):
        """docs/create-templates.md:34 documents the key as optional."""
        phab = _phab()

        result = _create(phab, [_task("Plain")])

        assert result["task_ids"] == [1]
        assert {"type": "title", "value": "Plain"} in _transactions(phab)

    @pytest.mark.parametrize("variables", [None, {}], ids=["null", "empty"])
    def test_an_empty_variables_key_creates_its_tasks(self, variables):
        phab = _phab()

        result = _create(phab, [_task("Plain")], config={"variables": variables})

        assert result["task_ids"] == [1]

    @pytest.mark.parametrize(
        "config",
        [{}, {"variables": None}, {"variables": {}}],
        ids=["missing", "null", "empty"],
    )
    def test_no_variables_is_never_a_key_error(self, config):
        """The CLI catches only the phabfive exceptions, so a KeyError is a traceback."""
        phab = _phab()

        try:
            _create(phab, [_task(projects=["Backend Team"])], config=config)
        except KeyError as exception:  # pragma: no cover - the bug, if it returns
            pytest.fail(f"bare KeyError: {exception!r}")

    def test_an_unrendered_variable_is_not_a_key_error_either(self):
        """Jinja2 answers an undefined variable with the empty string."""
        phab = _phab()

        result = _create(phab, [_task("Sprint {{ sprint_number }}")])

        assert {"type": "title", "value": "Sprint "} in _transactions(phab)
        assert result["task_ids"] == [1]

    @pytest.mark.parametrize(
        "variables",
        [["sprint"], "sprint", 42],
        ids=["list", "string", "number"],
    )
    def test_variables_written_as_anything_but_a_mapping_is_refused(self, variables):
        """Asking a list for .items() is a traceback the command cannot answer."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="variables takes a mapping"):
            _create(phab, [_task()], config={"variables": variables})

        phab.maniphest.edit.assert_not_called()


class TestTheCallersData:
    """A program hands the same dict in twice and gets the same result twice."""

    def test_the_config_is_not_modified(self):
        phab = _phab()
        config = {
            "variables": {"sprint_number": 42},
            "tasks": [_task("Sprint {{ sprint_number }}", priority="High")],
        }
        before = copy.deepcopy(config)

        _maniphest(phab).create_tasks_from_config(config)

        assert config == before

    def test_the_same_config_creates_the_same_tasks_twice(self):
        """The `variables` pop used to empty the caller's dict on the first run."""
        phab = _phab()
        config = {
            "variables": {"sprint_number": 42},
            "tasks": [_task("Sprint {{ sprint_number }}")],
        }

        maniphest = _maniphest(phab)
        maniphest.create_tasks_from_config(config)
        maniphest.create_tasks_from_config(config)

        titles = [
            t["value"]
            for call in phab.maniphest.edit.call_args_list
            for t in call.kwargs["transactions"]
            if t["type"] == "title"
        ]

        assert titles == ["Sprint 42", "Sprint 42"]


class TestARootThatIsNotATemplate:
    """An empty file loads to None, and the CLI has no handler for AttributeError."""

    @pytest.mark.parametrize("config", [None, [], "tasks"], ids=["null", "list", "str"])
    def test_it_is_refused(self, config):
        phab = _phab()

        with pytest.raises(PhabfiveDataException, match="mapping at the root"):
            _maniphest(phab).create_tasks_from_config(config)

    def test_an_empty_file_is_refused(self, tmp_path):
        template = tmp_path / "empty.yaml"
        template.write_text("")
        phab = _phab()

        with pytest.raises(PhabfiveDataException):
            _maniphest(phab).create_tasks_from_yaml(str(template))
