# -*- coding: utf-8 -*-

"""`phabfive.create`: one call over a spec, whatever object types it holds.

`phabfive.spec.create` is the *format* and may not import an app class;
this subpackage is the *runner* and is allowed to know that `Maniphest`,
`Project` and `Paste` exist. That split is the whole reason it is a
separate package, and it is the same one `phabfive/search/` already draws.

It had no tests of its own at all: `app_for` was reached only from
`phabfive.spec.create._project_app`, and `plan_spec`, `apply_plan` and
`apply_spec` - all three published in `phabfive.create.__all__` and in
`phabfive.__init__._SUBMODULES` - were executed by nothing in the suite.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.create import (
    CREATE_APPS,
    app_for,
    apply_plan,
    apply_spec,
    plan_spec,
)
from phabfive.maniphest.core import Maniphest
from phabfive.project.core import Project
from phabfive.spec import Spec
from phabfive.spec.create import CreatePlanError


def _phab():
    phab = MagicMock()
    phab.project.query.return_value = {"data": {}}
    phab.project.search.return_value = {"data": []}
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller", "userName": "caller"}
    phab.user.search.return_value = {"data": []}

    task_ids = iter(range(1, 100))
    project_ids = iter(range(100, 200))

    phab.maniphest.edit.side_effect = lambda transactions: {
        "object": {"id": next(task_ids), "phid": f"PHID-TASK-{next(task_ids)}"}
    }
    phab.project.edit.side_effect = lambda transactions: {
        "object": {"id": next(project_ids), "phid": "PHID-PROJ-100"}
    }

    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()

    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}

    return maniphest


SPEC = {
    "kind": "create",
    "projects": [{"id": "platform", "name": "Platform"}],
    "tasks": [{"title": "Bootstrap", "projects": ["$platform"]}],
}


def _spec(body=None):
    return Spec.from_data(body or SPEC, kind="create").render()


class TestAppFor:
    def test_every_creatable_type_has_a_row(self):
        from phabfive.spec.create import CREATABLE_TYPES

        assert CREATABLE_TYPES <= set(CREATE_APPS)

    def test_a_sibling_shares_the_parent_client_rather_than_connecting_again(self):
        """`_from_parent`, the rule `Diffusion.passphrase` already follows."""
        parent = _maniphest(_phab())

        sibling = app_for("project", parent)

        assert isinstance(sibling, Project)
        assert sibling.phab is parent.phab

    def test_the_parent_is_handed_back_when_it_is_already_the_right_class(self):
        parent = _maniphest(_phab())

        assert app_for("task", parent) is parent

    def test_something_that_is_not_an_app_is_handed_back_untouched(self):
        """Which is what lets a test stand in for an app."""
        stand_in = object()

        assert app_for("task", stand_in) is stand_in

    def test_an_object_type_nothing_creates_is_refused_by_name(self):
        with pytest.raises(CreatePlanError) as excinfo:
            app_for("repository", _maniphest(_phab()))

        assert excinfo.value.check == "unsupported-type"
        assert excinfo.value.object_type == "repository"
        assert "task" in str(excinfo.value)


class TestPlanSpec:
    def test_it_plans_the_whole_document_without_writing_anything(self):
        phab = _phab()

        plan = plan_spec(_maniphest(phab), _spec())

        assert [one.object_type for one in plan.items] == ["project", "task"]
        phab.project.edit.assert_not_called()
        phab.maniphest.edit.assert_not_called()

    def test_the_checks_can_be_turned_off_without_losing_the_references(self):
        phab = _phab()

        plan = plan_spec(_maniphest(phab), _spec(), validate=False)

        task = plan.by_path()["tasks[0]"]

        assert {"type": "projects.add", "value": ["$platform"]} in task.transactions


class TestApplying:
    def test_apply_plan_yields_one_record_per_object(self):
        phab = _phab()
        parent = _maniphest(phab)

        records = list(apply_plan(parent, plan_spec(parent, _spec())))

        assert [(one.object_type, one.status) for one in records] == [
            ("project", "created"),
            ("task", "created"),
        ]

    def test_apply_spec_is_the_same_run_as_a_report(self):
        phab = _phab()
        parent = _maniphest(phab)

        report = apply_spec(parent, plan_spec(parent, _spec()))

        assert report.ok
        assert len(report.created) == 2

    def test_the_project_is_created_through_project_edit(self):
        phab = _phab()
        parent = _maniphest(phab)

        apply_spec(parent, plan_spec(parent, _spec()))

        assert phab.project.edit.call_count == 1
        assert phab.maniphest.edit.call_count == 1

    def test_nothing_is_ever_sent_with_an_object_identifier(self):
        """The guarantee that makes anchoring to an existing object safe."""
        phab = _phab()
        parent = _maniphest(phab)

        apply_spec(parent, plan_spec(parent, _spec()))

        for endpoint in (phab.project.edit, phab.maniphest.edit):
            for call in endpoint.call_args_list:
                assert "objectIdentifier" not in call.kwargs
                assert not call.args
