# -*- coding: utf-8 -*-

"""Task policies: what a task shows, what an edit can move, and what it cannot.

Tasks reuse the grammar in ``phabfive/policy.py`` unchanged - that module is
covered by ``tests/test_policy.py`` and is not retested here. What is specific
to Maniphest is the third policy, the two transactions, and the four display
builders that all have to name the section the same way.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

from phabfive.constants import TASK_POLICY_FIELDS, TASK_POLICY_TRANSACTIONS
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest import Maniphest
from phabfive.maniphest.formatters import format_task_policy


@pytest.fixture
def maniphest():
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "http://phorge.localhost"
    return maniphest


def _task(**policy):
    """A task record as maniphest.search reports one, policies included."""
    return {
        "id": 42,
        "phid": "PHID-TASK-42",
        "fields": {
            "name": "Harden SSH configuration",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "High", "value": 80},
            "description": {"raw": ""},
            "ownerPHID": None,
            "policy": {"view": "users", "interact": "users", "edit": "users", **policy},
        },
        "attachments": {
            "columns": {"boards": {}},
            "projects": {"projectPHIDs": []},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


class TestTheThirdPolicy:
    """A task has three policies where a repository has three of its own, and
    only two of a task's can be written."""

    def test_a_task_carries_interact_and_a_repository_does_not(self):
        assert set(TASK_POLICY_FIELDS) == {"view", "interact", "edit"}

    def test_there_is_no_interact_transaction(self):
        """Confirmed against the instance, not assumed from the read side.

        maniphest.edit answers an unknown type by listing every valid one, and
        that list holds `view` and `edit` and nothing for interact: both
        `interact` and `policy.interact` are refused. ManiphestTask::getPolicy
        is why - a task does not store an interact policy, it returns its view
        policy unless its status locks comments, and then "no-one". So the way
        to move it is the status, not a policy.
        """
        assert TASK_POLICY_TRANSACTIONS == {"view": "view", "edit": "edit"}
        assert "interact" not in TASK_POLICY_TRANSACTIONS

    def test_no_edit_command_offers_an_interact_option(self):
        """Offering one would be offering a write that cannot happen."""
        from typer.main import get_command

        from phabfive.cli import app

        import click

        command = get_command(app)
        ctx = click.Context(command, info_name="phabfive")

        for path in (("edit",), ("maniphest", "edit"), ("maniphest", "create")):
            target = command
            context = ctx
            for name in path:
                target = target.get_command(context, name)
                context = click.Context(target, parent=context, info_name=name)

            options = {opt for param in target.params for opt in param.opts}

            assert "--visible-to" in options, path
            assert "--editable-by" in options, path
            assert "--interact" not in options, path


class TestFormatTaskPolicy:
    def test_keywords_are_labelled_the_way_the_web_ui_labels_them(self):
        assert format_task_policy(
            {"view": "public", "interact": "public", "edit": "admin"}
        ) == {
            "Visible To": "Public (No Login Required)",
            "Editable By": "Administrators",
            "Can Interact": "Public (No Login Required)",
        }

    def test_a_locked_task_says_so_through_interact(self):
        """The one case where interact differs from view, and the reason it is
        worth showing at all."""
        assert format_task_policy(
            {"view": "users", "interact": "no-one", "edit": "no-one"}
        ) == {
            "Visible To": "All Users",
            "Editable By": "No One",
            "Can Interact": "No One",
        }

    def test_a_phid_is_named_in_the_spelling_the_options_take(self):
        names = {"PHID-PROJ-infra": "#infrastructure"}

        record = format_task_policy(
            {"view": "PHID-PROJ-infra", "interact": "PHID-PROJ-infra", "edit": "users"},
            names,
        )

        assert record["Visible To"] == "#infrastructure"
        assert record["Can Interact"] == "#infrastructure"

    def test_an_unresolved_phid_is_shown_as_it_stands(self):
        """Inventing a name for a policy is worse than showing the PHID."""
        record = format_task_policy({"view": "PHID-PLCY-custom"})

        assert record["Visible To"] == "PHID-PLCY-custom"

    def test_a_task_with_no_policy_field_is_not_an_error(self):
        """An older instance, or a record fetched without them."""
        assert format_task_policy(None) == {
            "Visible To": "(none)",
            "Editable By": "(none)",
            "Can Interact": "(none)",
        }


def _display_data(maniphest, task, show_policy=True):
    """The task_dict the four display builders are handed."""
    with patch(
        "phabfive.maniphest.fetchers.fetch_project_names_for_boards",
        return_value={},
    ):
        maniphest.phab.user.search.return_value = {"data": []}
        result = maniphest._build_task_display_data([task], show_policy=show_policy)

    return result["tasks"][0]


class TestTheFourBuildersAgree:
    """rich, tree, yaml and json each construct the record from scratch.

    So a Policy section added to some and not others makes --format=yaml and
    --format=json disagree about the same task, which is the thing
    json_output.py's docstring promises cannot happen. Nothing structural stops
    it - the duplication is still there - so this is what notices.
    """

    _expected = {
        "Visible To": "#infrastructure",
        "Editable By": "@admin",
        "Can Interact": "#infrastructure",
    }

    @pytest.fixture
    def task_dict(self, maniphest):
        maniphest.phab.phid.query.return_value = {
            "PHID-PROJ-infra": {
                "type": "PROJ",
                "name": "Infrastructure",
                "uri": "http://phorge.localhost/tag/infrastructure/",
            },
            "PHID-USER-admin": {"type": "USER", "name": "admin"},
        }

        return _display_data(
            maniphest,
            _task(
                view="PHID-PROJ-infra",
                interact="PHID-PROJ-infra",
                edit="PHID-USER-admin",
            ),
        )

    def test_the_record_itself_names_the_policies(self, task_dict):
        assert task_dict["Policy"] == self._expected

    def test_json(self, task_dict):
        from phabfive.display import _build_task_json_output

        assert _build_task_json_output(task_dict)["Policy"] == self._expected

    def test_yaml(self, task_dict, capsys):
        from phabfive.display import _display_task_yaml

        _display_task_yaml(task_dict)

        parsed = YAML(typ="safe").load(StringIO(capsys.readouterr().out))

        assert parsed[0]["Policy"] == self._expected

    def test_rich(self, task_dict):
        """Rich is YAML-shaped for this section, so it is read back as YAML -
        which is also what keeps `View: #infrastructure` from round-tripping as
        an empty value plus a comment."""
        from phabfive.display import _display_task_rich

        printed = []
        console = MagicMock()
        console.print.side_effect = lambda line="", **kw: printed.append(str(line))

        phabfive_instance = MagicMock()
        phabfive_instance.url = "http://phorge.localhost"

        _display_task_rich(console, task_dict, phabfive_instance)

        # The Policy section on its own: the lines around it are the older
        # task fields, which still print bare and so are not YAML in general.
        output = "\n".join(printed)
        section = output[output.index("  Policy:") :]

        parsed = YAML(typ="safe").load(StringIO(section))

        assert parsed["Policy"] == self._expected

    def test_tree(self, task_dict):
        """A tree is not YAML, so its nodes are compared as nodes."""
        from phabfive.display import _display_task_tree

        console = MagicMock()
        phabfive_instance = MagicMock()
        phabfive_instance.url = "http://phorge.localhost"

        _display_task_tree(console, task_dict, phabfive_instance)

        tree = console.print.call_args[0][0]
        branch = next(node for node in tree.children if node.label == "Policy")

        assert [str(child.label) for child in branch.children] == [
            f"{key}: {value}" for key, value in self._expected.items()
        ]


class TestThePolicySectionIsOptIn:
    """`--show-policy` joined `-H`, `-M` and `-C`; before it, Policy was the
    one optional section nobody could decline.

    Naming a policy that carries a PHID costs a ``phid.query`` per page, so
    the gate is in the record builder and covers the resolution as well as
    the rendering. The resolution is the half that leaves no trace in the
    output, so it is counted rather than read.
    """

    def _task_naming_a_project(self):
        return _task(view="PHID-PROJ-infra")

    def test_no_phid_query_is_made_without_the_flag(self, maniphest):
        _display_data(maniphest, self._task_naming_a_project(), show_policy=False)

        maniphest.phab.phid.query.assert_not_called()

    def test_one_is_made_with_the_flag(self, maniphest):
        maniphest.phab.phid.query.return_value = {}

        _display_data(maniphest, self._task_naming_a_project(), show_policy=True)

        assert maniphest.phab.phid.query.call_count == 1
        assert maniphest.phab.phid.query.call_args[1]["phids"] == ["PHID-PROJ-infra"]

    def test_the_section_is_absent_until_asked_for(self, maniphest):
        task = self._task_naming_a_project()

        assert "Policy" not in _display_data(maniphest, task, show_policy=False)
        assert "Policy" in _display_data(maniphest, task, show_policy=True)

    @pytest.mark.parametrize("builder", ["json", "yaml", "rich", "tree"])
    def test_every_builder_agrees_that_it_is_absent(self, maniphest, capsys, builder):
        """The gate is in the record, so no builder can be the one that keeps
        printing it - the same promise TestTheFourBuildersAgree makes from the
        other side."""
        from phabfive.display import (
            _build_task_json_output,
            _display_task_rich,
            _display_task_tree,
            _display_task_yaml,
        )

        task_dict = _display_data(maniphest, _task(), show_policy=False)

        if builder == "json":
            assert "Policy" not in _build_task_json_output(task_dict)
            return

        if builder == "yaml":
            _display_task_yaml(task_dict)
            assert "Policy" not in capsys.readouterr().out
            return

        console = MagicMock()
        printed = []
        console.print.side_effect = lambda line="", **kw: printed.append(str(line))
        phabfive_instance = MagicMock()
        phabfive_instance.url = "http://phorge.localhost"

        if builder == "rich":
            _display_task_rich(console, task_dict, phabfive_instance)
            assert "Policy" not in "\n".join(printed)
            return

        _display_task_tree(console, task_dict, phabfive_instance)
        tree = console.print.call_args[0][0]

        assert not any(node.label == "Policy" for node in tree.children)

    def test_the_cli_threads_the_flag(self):
        from typer.testing import CliRunner

        from phabfive.cli.maniphest import maniphest_app

        for args, expected in (([], False), (["--show-policy"], True), (["-P"], True)):
            mock_maniphest = MagicMock()
            mock_maniphest.task_show.return_value = {"tasks": [], "missing_ids": []}

            with patch(
                "phabfive.cli.maniphest._get_maniphest_app",
                return_value=mock_maniphest,
            ):
                CliRunner().invoke(maniphest_app, ["show", "T1", *args])

            assert mock_maniphest.task_show.call_args[1]["show_policy"] is expected

    def test_search_offers_it_too(self):
        """`repo list` got one, so the other listing command does as well -
        without it a search has no way back to a section it used to print."""
        from typer.testing import CliRunner

        from phabfive.cli.maniphest import maniphest_app

        for args, expected in (([], False), (["--show-policy"], True)):
            mock_maniphest = MagicMock()
            mock_maniphest.task_search.return_value = {"tasks": []}

            with patch(
                "phabfive.cli.maniphest._get_maniphest_app",
                return_value=mock_maniphest,
            ):
                CliRunner().invoke(maniphest_app, ["search", "--tag=infra", *args])

            assert mock_maniphest.task_search.call_args[1]["show_policy"] is expected


class TestBuildPolicyEdit:
    """Policies are the one task field that is not a scalar: both ends of the
    change have to be resolved before either can be shown."""

    def test_the_transaction_names_are_the_ones_conduit_accepts(self, maniphest):
        """Confirmed against the instance: maniphest.edit lists `view` and
        `edit` among its valid types and refuses `policy.view` and
        `policy.edit`, the same way diffusion.repository.edit does."""
        transactions, _ = maniphest.build_task_edit(
            "42", _task(), visible_to="public", editable_by="admin"
        )

        assert transactions == [
            {"type": "view", "value": "public"},
            {"type": "edit", "value": "admin"},
        ]

    def test_the_change_names_both_ends(self, maniphest):
        _, changes = maniphest.build_task_edit("42", _task(), visible_to="public")

        assert changes == [
            {
                "field": "Visible To",
                "old": "All Users",
                "new": "Public (No Login Required)",
            }
        ]

    def test_a_project_is_resolved_and_then_named(self, maniphest):
        """A PHID either side of an arrow says nothing about what changed."""
        maniphest.phab.project.search.return_value = {
            "data": [{"phid": "PHID-PROJ-infra"}]
        }
        maniphest.phab.phid.query.return_value = {
            "PHID-PROJ-infra": {
                "type": "PROJ",
                "name": "Infrastructure",
                "uri": "http://phorge.localhost/tag/infrastructure/",
            }
        }

        transactions, changes = maniphest.build_task_edit(
            "42", _task(), visible_to="#infrastructure"
        )

        assert transactions == [{"type": "view", "value": "PHID-PROJ-infra"}]
        assert changes == [
            {"field": "Visible To", "old": "All Users", "new": "#infrastructure"}
        ]

    def test_a_policy_already_in_place_is_not_a_transaction(self, maniphest):
        transactions, changes = maniphest.build_task_edit(
            "42", _task(), visible_to="users"
        )

        assert (transactions, changes) == ([], [])

    def test_a_policy_outside_the_grammar_is_refused(self, maniphest):
        """Client-side, because Conduit reads an unrecognised value as a policy
        nobody satisfies and answers a typo with a self-lockout error."""
        with pytest.raises(PhabfiveConfigException, match="--visible-to"):
            maniphest.build_task_edit("42", _task(), visible_to="nonsense")

        maniphest.phab.maniphest.edit.assert_not_called()

    def test_policies_ride_along_with_the_scalar_fields(self, maniphest):
        transactions, changes = maniphest.build_task_edit(
            "42", _task(), title="A new title", visible_to="public"
        )

        assert [t["type"] for t in transactions] == ["title", "view"]
        assert [c["field"] for c in changes] == ["Title", "Visible To"]

    def test_both_ends_of_every_arrow_come_out_of_one_lookup(self, maniphest):
        maniphest.phab.project.search.return_value = {
            "data": [{"phid": "PHID-PROJ-new"}]
        }

        maniphest.build_task_edit(
            "42", _task(view="PHID-PROJ-old"), visible_to="#new", editable_by="public"
        )

        maniphest.phab.phid.query.assert_called_once()

    def test_a_self_lockout_is_reported_as_a_sentence(self, maniphest):
        """Phorge refuses the edit; the stack trace around it says nothing the
        sentence does not."""
        from phabricator import APIError

        maniphest.phab.maniphest.edit.side_effect = APIError(
            "ERR-CONDUIT-CORE",
            "Validation errors:\n  - The view policy of this object would no "
            "longer allow you to view the object.",
        )

        with pytest.raises(PhabfiveDataException) as excinfo:
            maniphest.apply_task_edit("42", [{"type": "view", "value": "no-one"}])

        message = str(excinfo.value)

        assert message.startswith("The view policy of this object")
        assert "Nothing was changed" in message


class TestCreateWithPolicies:
    def test_the_transactions_are_the_same_two(self, maniphest):
        result = maniphest.create_task(
            "A task", visible_to="public", editable_by="admin", dry_run=True
        )

        assert result["policy"] == {
            "Visible To": "Public (No Login Required)",
            "Editable By": "Administrators",
        }

    def test_a_policy_outside_the_grammar_is_refused_before_anything_else(
        self, maniphest
    ):
        """Before the tags, the assignee and the subscribers are resolved, so a
        typo costs no round trip at all."""
        with pytest.raises(PhabfiveConfigException, match="--editable-by"):
            maniphest.create_task("A task", editable_by="nonsense", dry_run=True)

        maniphest.phab.project.search.assert_not_called()


class TestTheBatchConfirmationGuard:
    """A policy change joins the guard that title and description changes are
    already behind: with no terminal to show the change on, it wants --yes.

    A retitled task is still where it was; one whose view policy has narrowed
    has simply gone, for everybody the new policy leaves out.
    """

    def _tasks(self, *policies):
        return [
            {"task_id": "42", "task_data": _task(**policy), "board_phid": None}
            for policy in policies
        ]

    def test_nothing_is_asked_when_no_policy_is_asked_for(self, maniphest):
        from phabfive.edit.batch import _needs_policy_confirmation

        assert not _needs_policy_confirmation(self._tasks({}), maniphest, None, None)

    def test_a_policy_that_would_change_asks(self, maniphest):
        from phabfive.edit.batch import _needs_policy_confirmation

        assert _needs_policy_confirmation(self._tasks({}), maniphest, "public", None)

    def test_a_policy_already_in_place_does_not_ask(self, maniphest):
        """The same rule the text guard follows: a no-op is not a change."""
        from phabfive.edit.batch import _needs_policy_confirmation

        assert not _needs_policy_confirmation(self._tasks({}), maniphest, "users", None)

    def test_one_task_out_of_several_is_enough(self, maniphest):
        from phabfive.edit.batch import _needs_policy_confirmation

        tasks = self._tasks({}, {"view": "public"})

        assert _needs_policy_confirmation(tasks, maniphest, "users", None)

    def test_a_project_is_resolved_once_for_the_whole_batch(self, maniphest):
        """Unlike the text guard, this one needs the instance: `#infra` has to
        become a PHID before it can be compared with the policy in place."""
        from phabfive.edit.batch import _needs_policy_confirmation

        maniphest.phab.project.search.return_value = {
            "data": [{"phid": "PHID-PROJ-infra"}]
        }
        tasks = self._tasks(
            {"view": "PHID-PROJ-infra"},
            {"view": "PHID-PROJ-infra"},
            {"view": "PHID-PROJ-infra"},
        )

        assert not _needs_policy_confirmation(tasks, maniphest, "#infra", None)
        maniphest.phab.project.search.assert_called_once()


class TestTheBatchPathEndToEnd:
    """The guard, and the round trip, as the batch loop actually runs them."""

    @staticmethod
    def _maniphest(**policy):
        maniphest = MagicMock()
        maniphest._get_task_data.return_value = _task(**policy)
        maniphest.build_task_edit.return_value = (
            [{"type": "view", "value": "public"}],
            [{"field": "Visible To", "old": "All Users", "new": "Public"}],
        )
        return maniphest

    @staticmethod
    def _tasks(n=2):
        return [{"object_id": str(100 + i)} for i in range(n)]

    def test_no_terminal_refuses_a_policy_change(self, capsys):
        """The decision this PR took: a policy change joins title and
        description behind the --yes guard rather than applying unreviewed."""
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with patch("sys.stdin.isatty", return_value=False):
            retcode = edit_tasks_batch(self._tasks(2), maniphest, visible_to="public")

        assert retcode == 1
        maniphest.apply_task_edit.assert_not_called()

        err = capsys.readouterr().err

        assert "--yes required for non-interactive mode" in err
        assert "No tasks were modified." in err

    def test_no_terminal_applies_a_policy_change_with_yes(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with patch("sys.stdin.isatty", return_value=False):
            retcode = edit_tasks_batch(
                self._tasks(2), maniphest, visible_to="public", force=True
            )

        assert retcode == 0
        assert maniphest.apply_task_edit.call_count == 2

    def test_the_policy_options_reach_build_task_edit(self):
        from phabfive.edit.batch import edit_tasks_batch

        maniphest = self._maniphest()

        with patch("sys.stdin.isatty", return_value=False):
            edit_tasks_batch(
                self._tasks(1),
                maniphest,
                visible_to="public",
                editable_by="admin",
                dry_run=True,
            )

        _, kwargs = maniphest.build_task_edit.call_args

        assert kwargs["visible_to"] == "public"
        assert kwargs["editable_by"] == "admin"

    def test_the_policy_section_survives_the_yaml_round_trip(self):
        """`phabfive --format=yaml maniphest show T1 | phabfive edit` must not
        trip over the section the show has just printed."""
        import contextlib

        from phabfive.display import display_tasks_yaml
        from phabfive.yaml_utils import parse_yaml_from_stdin

        task_dicts = [
            {
                "_url": "https://example.com/T123",
                "Task": {"Name": "Task 1", "Status": "Open"},
                "Policy": {
                    "Visible To": "#infrastructure",
                    "Editable By": "@admin",
                    "Can Interact": "#infrastructure",
                },
            }
        ]

        emitted = StringIO()
        with contextlib.redirect_stdout(emitted):
            display_tasks_yaml(task_dicts)

        def parse_monogram(link):
            return "task", link.rsplit("/T", 1)[1]

        with patch("sys.stdin", StringIO(emitted.getvalue())):
            objects = parse_yaml_from_stdin(parse_monogram)

        assert [obj["object_id"] for obj in objects] == ["123"]
        assert objects[0]["data"]["Policy"]["Visible To"] == "#infrastructure"


class TestTheCliThreadsThePolicyOptions:
    """Both edit entry points and create have to carry them, or a pipeline
    silently drops the policy it was asked for."""

    def _invoke(self, app, args):
        from typer.testing import CliRunner

        handler = MagicMock()
        handler.edit_objects.return_value = 0

        with (
            patch("phabfive.cli.maniphest._get_edit_app", return_value=handler),
            patch("phabfive.cli.edit._get_edit_app", return_value=handler),
        ):
            result = CliRunner().invoke(app, args)

        return result, handler

    def test_maniphest_edit(self):
        from phabfive.cli.maniphest import maniphest_app

        result, handler = self._invoke(
            maniphest_app, ["edit", "T42", "--visible-to=public", "--editable-by=admin"]
        )

        assert result.exit_code == 0
        _, kwargs = handler.edit_objects.call_args
        assert (kwargs["visible_to"], kwargs["editable_by"]) == ("public", "admin")

    def test_the_generic_edit(self):
        from phabfive.cli import app

        result, handler = self._invoke(
            app, ["edit", "T42", "--visible-to=public", "--editable-by=admin"]
        )

        assert result.exit_code == 0
        _, kwargs = handler.edit_objects.call_args
        assert (kwargs["visible_to"], kwargs["editable_by"]) == ("public", "admin")

    def test_maniphest_create(self):
        from typer.testing import CliRunner

        from phabfive.cli.maniphest import maniphest_app

        maniphest = MagicMock()
        maniphest.create_task.return_value = {"dry_run": True, "title": "A task"}

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = CliRunner().invoke(
                maniphest_app,
                ["create", "A task", "--visible-to=public", "--dry-run"],
            )

        assert result.exit_code == 0
        _, kwargs = maniphest.create_task.call_args
        assert kwargs["visible_to"] == "public"
