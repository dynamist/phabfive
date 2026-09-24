# -*- coding: utf-8 -*-

"""Options and priorities of a creation template (#465).

`maniphest create --with` used to take a template's `priority` on trust, so a
typo in it reached the server and came back as an opaque Conduit error rather
than naming the valid priorities the way `--priority` does. And every option
given alongside `--with` was accepted and then dropped in silence.

Both are pinned here: a priority is validated the way the flag is, before
anything is fetched, and an option the template path cannot honour is refused
instead of ignored. `--dry-run` and the global `--format` are honoured, so
they are not refused.
"""

# python std lib
import inspect
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app
from phabfive.cli.maniphest import (
    _CREATE_OPTIONS_IGNORED_BY_TEMPLATE,
    create,
)
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException
from phabfive.maniphest.core import Maniphest

runner = CliRunner()


@pytest.fixture(autouse=True)
def restore_output_format():
    """Put the global output format back, even when a test fails.

    Phabfive._output_format is class state, so a test that leaves it set
    changes what every later test renders.
    """
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


def _phab():
    """A client that answers every lookup a one-task template makes."""
    phab = MagicMock()
    # The template path fetches projects with project.query, not project.search
    phab.project.query.return_value = {"data": {}}
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller", "userName": "caller"}
    phab.user.search.return_value = {"data": []}
    phab.maniphest.edit.return_value = {
        "object": {"id": 1, "phid": "PHID-TASK-1"},
    }
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


def _create(phab, tasks, dry_run=False):
    return _maniphest(phab).create_tasks_from_config({"tasks": tasks}, dry_run=dry_run)


def _task(title="Task", **fields):
    return {"title": title, "description": "x", **fields}


def _transactions(phab, call=0):
    return phab.maniphest.edit.call_args_list[call].kwargs["transactions"]


def _value(phab, transaction_type, call=0):
    return next(
        t["value"] for t in _transactions(phab, call) if t["type"] == transaction_type
    )


def a_maniphest():
    """A Maniphest whose template creation and show are both answered."""
    instance = MagicMock()
    instance.create_tasks_from_yaml.return_value = {"task_ids": [7]}
    instance.task_show.return_value = {
        "tasks": [
            {
                "_url": "https://phorge.example.com/T7",
                "Task": {"Name": "probe", "Status": "Open", "Priority": "Normal"},
            }
        ],
        "missing_ids": [],
    }
    return instance


def _template(tmp_path):
    template = tmp_path / "tasks.yaml"
    template.write_text(
        "spec: phorge/v1alpha1\nkind: create\ntasks:\n  - title: Parent\n"
    )
    return template


@pytest.fixture
def planner():
    """The planner `--with` reaches, stubbed, so these tests are the command's.

    `--with` is `phabfive apply -f` under its old name
    (`phabfive.cli.create_spec`), so what it does with a file is
    `phabfive.create.plan_spec` and then `phabfive.create.apply_plan`. What
    is under test here is which options the command refuses and which it
    honours, none of which needs a real plan - and a `MagicMock` app cannot
    produce one.

    Yields ``(plan_spec, apply_plan)``, both patched.
    """
    import phabfive.create
    from phabfive.spec.create import CreateItem, CreatePlan, CreateRecord

    plan = CreatePlan(
        items=(
            CreateItem(
                object_type="task", path="tasks[0]", display={"title": "Parent"}
            ),
        )
    )
    records = [
        CreateRecord(
            object_type="task",
            path="tasks[0]",
            status="created",
            id=7,
            monogram="T7",
            title="Parent",
        )
    ]

    with patch.object(phabfive.create, "plan_spec", return_value=plan) as plan_spec:
        with patch.object(
            phabfive.create, "apply_plan", return_value=iter(records)
        ) as apply_plan:
            yield plan_spec, apply_plan


class TestTemplatePriority:
    """A template's priority is validated the way --priority is."""

    def test_a_typo_names_the_valid_priorities(self):
        phab = _phab()

        with pytest.raises(PhabfiveConfigException) as excinfo:
            _create(phab, [_task(priority="hgih")])

        message = str(excinfo.value)
        assert "hgih" in message
        assert "Unbreak, Triage, High, Normal, Low, Wish" in message

    def test_it_fails_before_any_request(self):
        """Nothing is fetched, so nothing is half-created either."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException):
            _create(phab, [_task(priority="hgih")])

        # The project fetch is what the validation had to move ahead of, so
        # it is the call with teeth here: project.search is never made at all
        phab.project.query.assert_not_called()
        phab.maniphest.edit.assert_not_called()

    def test_a_child_task_is_validated_too(self):
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="hgih"):
            _create(phab, [_task(tasks=[_task("Child", priority="hgih")])])

        phab.maniphest.edit.assert_not_called()

    @pytest.mark.parametrize(
        "priority, expected",
        [
            ("high", "high"),
            ("High", "high"),
            ("Unbreak Now!", "unbreak"),
            ("wishlist", "wish"),
        ],
    )
    def test_every_spelling_reaches_the_server_normalized(self, priority, expected):
        phab = _phab()

        _create(phab, [_task(priority=priority)])

        assert _value(phab, "priority") == expected

    def test_without_one_the_default_is_sent(self):
        phab = _phab()

        _create(phab, [_task()])

        assert _value(phab, "priority") == "normal"

    def test_an_empty_one_is_the_default_too(self):
        """`priority:` with nothing after it is no priority at all."""
        phab = _phab()

        _create(phab, [_task(priority=None)])

        assert _value(phab, "priority") == "normal"

    def test_a_number_is_refused(self):
        """A YAML scalar that is not a name never named a priority."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException, match="priority takes"):
            _create(phab, [_task(priority=2)])

        phab.maniphest.edit.assert_not_called()

    def test_the_cli_reports_it(self, tmp_path):
        import phabfive.create

        maniphest = MagicMock()
        template = _template(tmp_path)
        refusal = PhabfiveConfigException(
            "Invalid priority 'hgih'. Valid choices: Unbreak, Triage, High, "
            "Normal, Low, Wish"
        )

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            with patch.object(phabfive.create, "plan_spec", side_effect=refusal):
                result = runner.invoke(
                    app, ["maniphest", "create", "--with", str(template)]
                )

        assert result.exit_code == 1
        assert "Invalid priority 'hgih'" in result.stderr


class TestOptionsAlongsideTemplate:
    """An option the template path cannot honour is refused, not ignored."""

    @pytest.mark.parametrize(
        "argv, option",
        [
            (["--tag", "X"], "--tag"),
            (["--column", "Backlog"], "--column"),
            (["--assign", "alice"], "--assign"),
            (["--status", "resolved"], "--status"),
            (["--priority", "high"], "--priority"),
            (["--add-subscriber", "alice"], "--add-subscriber"),
            (["--subscribe", "alice"], "--subscribe"),
            (["--space", "S3"], "--space"),
            (["--visible-to", "admin"], "--visible-to"),
            (["--editable-by", "admin"], "--editable-by"),
            (["--description", "text"], "--description"),
            (["--yes"], "--yes"),
            (["--interactive"], "--interactive"),
        ],
    )
    def test_it_is_refused_by_name(self, tmp_path, argv, option, planner):
        maniphest = a_maniphest()
        template = _template(tmp_path)
        plan_spec, _ = planner

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["maniphest", "create", "--with", str(template), *argv]
            )

        assert result.exit_code != 0
        assert option in result.stderr
        plan_spec.assert_not_called()

    def test_a_title_is_refused_too(self, tmp_path, planner):
        """It was dropped as silently as the options were."""
        maniphest = a_maniphest()
        template = _template(tmp_path)
        plan_spec, _ = planner

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["maniphest", "create", "Fix bug", "--with", str(template)]
            )

        assert result.exit_code != 0
        assert "--with cannot be combined with" in result.stderr
        plan_spec.assert_not_called()

    def test_every_offending_option_is_named(self, tmp_path):
        maniphest = a_maniphest()
        template = _template(tmp_path)

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                [
                    "maniphest",
                    "create",
                    "--with",
                    str(template),
                    "--tag",
                    "X",
                    "--priority",
                    "high",
                ],
            )

        assert result.exit_code != 0
        assert "--tag" in result.stderr
        assert "--priority" in result.stderr

    def test_a_default_that_was_not_passed_is_not_refused(self, tmp_path, planner):
        """--with alone still works: no option was given to drop."""
        maniphest = a_maniphest()
        template = _template(tmp_path)
        plan_spec, apply_plan = planner

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["maniphest", "create", "--with", str(template)]
            )

        assert result.exit_code == 0, result.output
        plan_spec.assert_called_once()
        apply_plan.assert_called_once()

    def test_dry_run_is_honoured_not_refused(self, tmp_path, planner):
        maniphest = a_maniphest()
        template = _template(tmp_path)
        plan_spec, apply_plan = planner

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                [
                    "--format=rich",
                    "maniphest",
                    "create",
                    "--with",
                    str(template),
                    "--dry-run",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "Parent" in result.stdout
        plan_spec.assert_called_once()
        # A dry run stops at the plan: nothing is written.
        apply_plan.assert_not_called()

    @pytest.mark.parametrize("output_format", ["json", "yaml", "jsonl", "rich"])
    def test_the_global_format_is_honoured_not_refused(
        self, tmp_path, output_format, planner
    ):
        maniphest = a_maniphest()
        template = _template(tmp_path)
        plan_spec, _ = planner

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                [
                    f"--format={output_format}",
                    "maniphest",
                    "create",
                    "--with",
                    str(template),
                ],
            )

        assert result.exit_code == 0, result.output
        plan_spec.assert_called_once()


class TestRefusedOptionsAreRealOptions:
    """Every name in the refusal list is one `create` actually has.

    `ctx.get_parameter_source()` answers None for a name that is not a
    parameter, so a typo or a rename in the map un-refuses that option
    without a test noticing.
    """

    def test_every_key_names_a_parameter(self):
        names = set(inspect.signature(create).parameters)
        unknown = sorted(set(_CREATE_OPTIONS_IGNORED_BY_TEMPLATE) - names)

        assert not unknown, (
            f"{unknown} is refused alongside --with under a name `create` has "
            "no parameter for, so nothing is refused"
        )

    def test_every_option_is_reported_as_it_is_typed(self):
        """The message names the option, so the spelling has to be a real flag."""
        parameters = inspect.signature(create).parameters

        for name, option in _CREATE_OPTIONS_IGNORED_BY_TEMPLATE.items():
            if not option.startswith("-"):  # the positional TITLE
                continue

            if name not in parameters:  # reported by the test above
                continue

            decls = getattr(parameters[name].default, "param_decls", ()) or ()

            assert option in decls, (
                f"{name} is reported as {option}, which is not one of its flags"
            )
