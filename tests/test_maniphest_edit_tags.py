# -*- coding: utf-8 -*-

"""`--tag` adds a task to projects and `--untag` removes it, from `maniphest
edit` and `phabfive edit` alike (#514).

Before this, `--tag` on an edit was only the board for `--column`, and with
no other option it fell through to $EDITOR; nothing sent projects.remove.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app as main_app
from phabfive.cli.maniphest import maniphest_app
from phabfive.exceptions import (
    PhabfiveInputException,
    PhabfiveNotFoundException,
    PhabfiveValidationException,
)
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.resolvers import resolve_project_tags

runner = CliRunner()

# project.query's shape: PHID -> {name, slugs}. "Sprint 1" is shared by two
# milestones, so it names neither.
PROJECTS = {
    "PHID-PROJ-backend": {"name": "Backend", "slugs": ["backend"]},
    "PHID-PROJ-qa": {"name": "QA", "slugs": ["qa", "quality"]},
    "PHID-PROJ-triage": {"name": "Triage", "slugs": ["triage"]},
    "PHID-PROJ-s1a": {"name": "Sprint 1", "slugs": []},
    "PHID-PROJ-s1b": {"name": "Sprint 1", "slugs": []},
}

IDS = {
    "PHID-PROJ-backend": 1,
    "PHID-PROJ-qa": 2,
    "PHID-PROJ-triage": 3,
    "PHID-PROJ-s1a": 4,
    "PHID-PROJ-s1b": 5,
}


def _project_search(constraints):
    """project.search, by PHID or by ID, over PROJECTS."""
    phids = constraints.get("phids") or [
        phid for phid, pid in IDS.items() if pid in constraints.get("ids", [])
    ]
    return {
        "data": [
            {"id": IDS[phid], "phid": phid, "fields": {"name": PROJECTS[phid]["name"]}}
            for phid in phids
            if phid in PROJECTS
        ]
    }


def _phab():
    phab = MagicMock()
    phab.project.query.return_value = {"data": PROJECTS}
    phab.project.search.side_effect = lambda constraints: _project_search(constraints)
    return phab


def _maniphest():
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = _phab()
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


def _task(projects=(), boards=None):
    return {
        "id": 42,
        "phid": "PHID-TASK-42",
        "fields": {
            "name": "A task",
            "status": {"name": "Open", "value": "open"},
            "priority": {"name": "High", "value": 80},
            "description": {"raw": ""},
            "ownerPHID": None,
        },
        "attachments": {
            "columns": {"boards": boards or {}},
            "projects": {"projectPHIDs": list(projects)},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


class TestResolveProjectTags:
    def test_every_spelling_resolves_to_the_project_and_its_name(self):
        resolved = resolve_project_tags(
            _phab(), ["backend", "#quality", "Triage", "PHID-PROJ-qa", "3"]
        )

        assert resolved == {
            "backend": ("PHID-PROJ-backend", "Backend"),
            "#quality": ("PHID-PROJ-qa", "QA"),
            "Triage": ("PHID-PROJ-triage", "Triage"),
            "PHID-PROJ-qa": ("PHID-PROJ-qa", "QA"),
            "3": ("PHID-PROJ-triage", "Triage"),
        }

    def test_nothing_asked_nothing_looked_up(self):
        phab = _phab()

        assert resolve_project_tags(phab, []) == {}
        phab.project.query.assert_not_called()

    def test_a_wildcard_is_refused_before_any_lookup(self):
        """An edit must not fan out to every project a pattern matches."""
        phab = _phab()

        with pytest.raises(PhabfiveInputException, match="--untag.*'Sprint\\*'"):
            resolve_project_tags(phab, ["Sprint*"], option="--untag")

        phab.project.query.assert_not_called()

    def test_every_unknown_project_is_named(self):
        with pytest.raises(PhabfiveNotFoundException, match="nope, gone"):
            resolve_project_tags(_phab(), ["nope", "backend", "gone"])

    def test_a_shared_name_is_ambiguous(self):
        with pytest.raises(PhabfiveInputException, match="ambiguous"):
            resolve_project_tags(_phab(), ["Sprint 1"])


class TestBuildTaskEdit:
    def test_a_new_tag_is_added_by_name(self):
        transactions, changes = _maniphest().build_task_edit(
            "42", _task(), tag=["backend"]
        )

        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-backend"]}
        ]
        assert changes == [{"field": "Tags", "old": None, "new": "Added: Backend"}]

    def test_tags_are_comma_separated_and_repeatable(self):
        transactions, _ = _maniphest().build_task_edit(
            "42", _task(), tag=["backend,qa", "triage"]
        )

        assert transactions == [
            {
                "type": "projects.add",
                "value": ["PHID-PROJ-backend", "PHID-PROJ-qa", "PHID-PROJ-triage"],
            }
        ]

    def test_a_tag_the_task_has_is_removed(self):
        transactions, changes = _maniphest().build_task_edit(
            "42", _task(["PHID-PROJ-triage"]), untag=["#triage"]
        )

        assert transactions == [
            {"type": "projects.remove", "value": ["PHID-PROJ-triage"]}
        ]
        assert changes == [{"field": "Tags", "old": None, "new": "Removed: Triage"}]

    def test_only_a_change_is_sent(self):
        """A tag already on the task is not added, one not on it not removed."""
        transactions, changes = _maniphest().build_task_edit(
            "42", _task(["PHID-PROJ-backend"]), tag=["Backend"], untag=["qa"]
        )

        assert transactions == []
        assert changes == []

    @pytest.mark.parametrize("untag", ["qa", "#quality", "PHID-PROJ-qa", "2"])
    def test_adding_and_removing_one_project_is_refused(self, untag):
        """Compared by PHID, however each was spelled."""
        with pytest.raises(PhabfiveInputException, match="Cannot both add and remove"):
            _maniphest().build_task_edit("42", _task(), tag=["QA"], untag=[untag])

    def test_one_project_named_twice_is_sent_once(self):
        transactions, changes = _maniphest().build_task_edit(
            "42", _task(), tag=["backend,#backend", "1", "PHID-PROJ-backend"]
        )

        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-backend"]}
        ]
        assert changes == [{"field": "Tags", "old": None, "new": "Added: Backend"}]

    def test_resolved_tags_are_not_looked_up_again(self):
        maniphest = _maniphest()

        transactions, _ = maniphest.build_task_edit(
            "42", _task(), tag={"b": ("PHID-PROJ-backend", "Backend")}
        )

        maniphest.phab.project.query.assert_not_called()
        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-backend"]}
        ]


class TestTagsWithColumn:
    COLUMNS = {
        "PHID-PROJ-qa": {"PHID-PCOL-qa-done": {"name": "Done", "sequence": 0}},
        "PHID-PROJ-backend": {
            "PHID-PCOL-backend-done": {"name": "Done", "sequence": 0}
        },
    }

    def _build(self, task, **kwargs):
        maniphest = _maniphest()
        with (
            patch(
                "phabfive.maniphest.fetchers.get_column_info",
                side_effect=lambda phab, board: self.COLUMNS[board],
            ),
            patch.object(
                Maniphest, "_navigate_column", return_value="PHID-PCOL-qa-done"
            ),
        ):
            return maniphest.build_task_edit(
                "42", task, column="Done", board_phids=["PHID-PROJ-qa"], **kwargs
            )

    def test_the_board_is_added_once_with_the_other_tags(self):
        """One projects.add, the board not twice, and before the move."""
        transactions, changes = self._build(_task(), tag=["QA", "backend"])

        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-qa", "PHID-PROJ-backend"]},
            {"type": "column", "value": ["PHID-PCOL-qa-done"]},
        ]
        assert changes == [
            {"field": "Tags", "old": None, "new": "Added: QA, Backend"},
            {"field": "Column", "old": "(none)", "new": "Done"},
        ]

    def test_a_board_that_is_not_a_tag_joins_the_same_transaction(self):
        transactions, _ = self._build(_task(), tag=["backend"])

        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-backend", "PHID-PROJ-qa"]},
            {"type": "column", "value": ["PHID-PCOL-qa-done"]},
        ]

    def test_the_board_named_twice_is_added_once(self):
        """The first --tag is the board, and repeated under other spellings."""
        transactions, _ = self._build(_task(), tag=["QA,#quality", "2", "backend"])

        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-qa", "PHID-PROJ-backend"]},
            {"type": "column", "value": ["PHID-PCOL-qa-done"]},
        ]

    @pytest.mark.parametrize("untag", ["QA", "#quality", "PHID-PROJ-qa"])
    def test_untagging_the_board_of_the_move_is_refused(self, untag):
        """An auto-detected board is no --tag, so only this check sees it."""
        task = _task(
            ["PHID-PROJ-qa"],
            boards={"PHID-PROJ-qa": {"columns": [{"phid": "PHID-PCOL-x"}]}},
        )

        with pytest.raises(PhabfiveInputException, match="Cannot both remove QA"):
            self._build(task, untag=[untag])

    def test_untagging_another_project_with_a_move_is_fine(self):
        task = _task(
            ["PHID-PROJ-qa", "PHID-PROJ-triage"],
            boards={"PHID-PROJ-qa": {"columns": [{"phid": "PHID-PCOL-x"}]}},
        )

        transactions, _ = self._build(task, untag=["triage"])

        assert transactions == [
            {"type": "projects.remove", "value": ["PHID-PROJ-triage"]},
            {"type": "column", "value": ["PHID-PCOL-qa-done"]},
        ]

    def test_a_board_the_task_is_on_is_not_added(self):
        transactions, _ = self._build(
            _task(
                ["PHID-PROJ-qa"],
                boards={"PHID-PROJ-qa": {"columns": [{"phid": "PHID-PCOL-x"}]}},
            ),
            tag=["QA"],
        )

        assert transactions == [{"type": "column", "value": ["PHID-PCOL-qa-done"]}]


@pytest.fixture
def mock_config(monkeypatch):
    """Configuration and a client that need no network."""
    monkeypatch.setenv("PHAB_TOKEN", "cli-testtoken1234567890123456789")
    monkeypatch.setenv("PHAB_URL", "https://phabricator.example.com/api/")
    monkeypatch.setattr("phabfive.core.Phabricator", lambda **kwargs: MagicMock())


class TestAColumnMovesOnEveryBoardNamed:
    """A task has a position on each board it is on, so --tag=a,b is two moves."""

    COLUMNS = {
        "PHID-PROJ-qa": {"PHID-PCOL-qa-done": {"name": "Done", "sequence": 0}},
        "PHID-PROJ-backend": {
            "PHID-PCOL-backend-done": {"name": "Done", "sequence": 0}
        },
    }

    def _build(self, task, boards, **kwargs):
        maniphest = _maniphest()
        columns = {
            "PHID-PROJ-qa": "PHID-PCOL-qa-done",
            "PHID-PROJ-backend": "PHID-PCOL-backend-done",
        }
        with (
            patch(
                "phabfive.maniphest.fetchers.get_column_info",
                side_effect=lambda phab, board: self.COLUMNS[board],
            ),
            patch.object(
                Maniphest,
                "_navigate_column",
                side_effect=lambda task_id, data, column, board: columns[board],
            ),
        ):
            return maniphest.build_task_edit(
                "42", task, column="Done", board_phids=boards, **kwargs
            )

    def test_both_moves_travel_as_one_column_transaction(self):
        """The field is documented as "List of columns to move the task to"."""
        transactions, _ = self._build(
            _task(["PHID-PROJ-qa", "PHID-PROJ-backend"]),
            ["PHID-PROJ-qa", "PHID-PROJ-backend"],
        )

        assert [t for t in transactions if t["type"] == "column"] == [
            {
                "type": "column",
                "value": ["PHID-PCOL-qa-done", "PHID-PCOL-backend-done"],
            }
        ]

    def test_each_move_says_which_board_it_is_on(self):
        """Two rows both reading "Done" would not say what is happening."""
        _, changes = self._build(
            _task(["PHID-PROJ-qa", "PHID-PROJ-backend"]),
            ["PHID-PROJ-qa", "PHID-PROJ-backend"],
        )

        assert [c["new"] for c in changes if c["field"] == "Column"] == [
            "Done on QA",
            "Done on Backend",
        ]

    def test_one_board_leaves_the_move_unlabelled(self):
        """Naming the board is only worth the noise when there are several."""
        _, changes = self._build(_task(["PHID-PROJ-qa"]), ["PHID-PROJ-qa"])

        assert [c["new"] for c in changes if c["field"] == "Column"] == ["Done"]

    def test_a_board_the_task_is_not_on_is_joined_before_the_move(self):
        """A card cannot be placed in a column of a board it is not on."""
        transactions, _ = self._build(
            _task(["PHID-PROJ-qa"]), ["PHID-PROJ-qa", "PHID-PROJ-backend"]
        )

        types = [t["type"] for t in transactions]
        assert types.index("projects.add") < types.index("column")
        [add] = [t for t in transactions if t["type"] == "projects.add"]
        assert add["value"] == ["PHID-PROJ-backend"]

    def test_untagging_any_board_of_the_move_is_refused(self):
        """The refusal covered the one board; it covers each of them now."""
        with pytest.raises(PhabfiveInputException, match="Cannot both remove"):
            self._build(
                _task(["PHID-PROJ-qa", "PHID-PROJ-backend"]),
                ["PHID-PROJ-qa", "PHID-PROJ-backend"],
                untag=["backend"],
            )


class TestPlan:
    """The batch resolves the tags once, and refuses before fetching a task."""

    @pytest.fixture
    def edit(self, mock_config):
        from phabfive.edit import Edit

        app = Edit()
        app.maniphest = _maniphest()
        app.maniphest._get_task_data = MagicMock(return_value=_task())
        return app

    def test_tags_are_resolved_once_for_the_batch(self, edit):
        plan = edit.plan("T1,T2,T3", tag="backend", untag="triage")

        assert edit.maniphest.phab.project.query.call_count == 2
        assert [e.transactions for e in plan.edits] == [
            [{"type": "projects.add", "value": ["PHID-PROJ-backend"]}]
        ] * 3

    def test_an_unknown_project_is_refused_before_any_task(self, edit):
        with pytest.raises(PhabfiveNotFoundException, match="nope"):
            edit.plan("T1,T2", tag=["backend", "nope"])

        edit.maniphest._get_task_data.assert_not_called()

    def test_tag_and_untag_of_one_project_is_refused_before_any_task(self, edit):
        with pytest.raises(PhabfiveInputException, match="Cannot both add and remove"):
            edit.plan("T1,T2", tag="Backend", untag="#backend")

        edit.maniphest._get_task_data.assert_not_called()

    def test_every_tag_is_a_board_for_column(self, edit):
        """A task has a position on each board, so --tag=a,b is two moves."""
        with patch("phabfive.edit.plan.validate_board_column_context") as validate:
            validate.return_value = (["PHID-PROJ-qa", "PHID-PROJ-backend"], None)
            edit.maniphest.build_task_edit = MagicMock(return_value=([], []))

            edit.plan("T1", tag=["qa,backend"], column="Done")

        assert validate.call_args.args[3] == ["PHID-PROJ-qa", "PHID-PROJ-backend"]
        kwargs = edit.maniphest.build_task_edit.call_args.kwargs
        assert kwargs["board_phids"] == ["PHID-PROJ-qa", "PHID-PROJ-backend"]
        assert kwargs["tag"] == {
            "qa": ("PHID-PROJ-qa", "QA"),
            "backend": ("PHID-PROJ-backend", "Backend"),
        }

    def test_each_task_in_a_batch_keeps_its_own_board(self, edit):
        """A board auto-detected for one task is not the next task's board."""
        on = {
            "1": _task(
                ["PHID-PROJ-backend"],
                boards={"PHID-PROJ-backend": {"columns": []}},
            ),
            "2": _task(["PHID-PROJ-qa"], boards={"PHID-PROJ-qa": {"columns": []}}),
        }
        edit.maniphest._get_task_data.side_effect = lambda task_id: on[task_id]
        edit.maniphest.build_task_edit = MagicMock(return_value=([], []))

        edit.plan("T1,T2", column="Done")

        assert [
            c.kwargs["board_phids"]
            for c in edit.maniphest.build_task_edit.call_args_list
        ] == [["PHID-PROJ-backend"], ["PHID-PROJ-qa"]]

    def test_a_task_on_several_boards_after_one_on_a_single_board_is_refused(
        self, edit
    ):
        on = {
            "1": _task(boards={"PHID-PROJ-backend": {"columns": []}}),
            "2": _task(
                boards={
                    "PHID-PROJ-qa": {"columns": []},
                    "PHID-PROJ-triage": {"columns": []},
                }
            ),
        }
        edit.maniphest._get_task_data.side_effect = lambda task_id: on[task_id]

        with pytest.raises(PhabfiveValidationException) as raised:
            edit.plan("T1,T2", column="Done")

        assert [p.task_id for p in raised.value.problems] == ["2"]

    def test_a_tag_already_on_every_task_is_a_noop(self, edit):
        edit.maniphest._get_task_data.return_value = _task(["PHID-PROJ-backend"])

        plan = edit.plan("T1,T2", tag="backend")

        assert all(e.noop for e in plan.edits)


class TestRunEdit:
    @pytest.mark.parametrize("option", ["tag", "untag"])
    def test_alone_it_does_not_open_the_editor(self, mock_config, option):
        from phabfive.cli.edit_flow import run_edit
        from phabfive.edit import Edit

        with patch("phabfive.cli.edit_flow._edit_task_single", return_value=0) as one:
            run_edit(Edit(), object_id="T123", **{option: ["Backend"]})

        assert one.call_args.kwargs[option] == ["Backend"]
        assert one.call_args.kwargs["edit_description_in_editor"] is False

    def test_it_reaches_the_batch_path(self, mock_config):
        from phabfive.cli.edit_flow import run_edit
        from phabfive.edit import Edit

        with patch("phabfive.cli.edit_flow.edit_tasks_batch", return_value=0) as batch:
            run_edit(Edit(), object_id="T123,T124", tag=["a"], untag=["b"])

        assert batch.call_args.kwargs["tag"] == ["a"]
        assert batch.call_args.kwargs["untag"] == ["b"]

    def test_it_reaches_the_stdin_path(self, mock_config):
        from phabfive.cli.edit_flow import run_edit
        from phabfive.edit import Edit

        objects = [{"object_type": "task", "object_id": "1", "data": {}}]
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("phabfive.cli.edit_flow.parse_yaml_from_stdin", return_value=objects),
            patch("phabfive.cli.edit_flow.edit_tasks_batch", return_value=0) as batch,
        ):
            run_edit(Edit(), untag=["b"])

        assert batch.call_args.kwargs["untag"] == ["b"]

    def test_the_dry_run_names_the_projects(self, mock_config, capsys):
        from phabfive.cli.edit_flow import edit_tasks_batch

        maniphest = _maniphest()
        maniphest._get_task_data = MagicMock(return_value=_task(["PHID-PROJ-qa"]))

        status = edit_tasks_batch(
            [{"object_id": "1"}, {"object_id": "2"}],
            maniphest,
            tag=["backend"],
            untag=["qa"],
            dry_run=True,
        )

        out = capsys.readouterr().out
        assert status == 0
        assert "Added: Backend" in out
        assert "Removed: QA" in out
        assert "PHID-PROJ" not in out
        maniphest.phab.maniphest.edit.assert_not_called()


class TestCommands:
    APPS = [
        (maniphest_app, "phabfive.cli.maniphest._get_edit_app"),
        (main_app, "phabfive.cli.edit._get_edit_app"),
    ]

    def _invoke(self, app, getter, *options):
        with (
            patch(getter),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(app, ["edit", "T1", *options])

        assert result.exit_code == 0, result.output
        return run_edit.call_args.kwargs

    @pytest.mark.parametrize("app, getter", APPS)
    def test_the_options_and_their_aliases_reach_run_edit(self, app, getter):
        kwargs = self._invoke(
            app,
            getter,
            "--tag=A,B",
            "--add-tag=C",
            "--untag=D",
            "--remove-tag=E",
            "--untag=F",
        )

        # Split later, by the plan, which is what keeps "A,B" one value here
        assert kwargs["tag"] == ["A,B", "C"]
        assert kwargs["untag"] == ["D", "F", "E"]

    @pytest.mark.parametrize("app, getter", APPS)
    def test_nothing_given_is_empty(self, app, getter):
        kwargs = self._invoke(app, getter, "--status=open")

        assert kwargs["tag"] == []
        assert kwargs["untag"] == []

    def test_both_commands_take_the_same_options(self):
        import typer.main

        def options(command):
            return {
                (opt, param.hidden)
                for param in command.params
                for opt in getattr(param, "opts", [])
                if opt.startswith("--")
            }

        maniphest_edit = typer.main.get_command(maniphest_app).commands["edit"]
        phabfive_edit = typer.main.get_command(main_app).commands["edit"]

        assert {("--untag", False), ("--remove-tag", True), ("--add-tag", True)} <= (
            options(phabfive_edit)
        )
        # `maniphest edit` also has the hidden --title for a positional title
        assert options(maniphest_edit) - {("--title", True)} == options(phabfive_edit)


class _Result(dict):
    """The client's answer: indexable, and its payload again as `.response`."""

    @property
    def response(self):
        return self


class TestEndToEnd:
    """From the command line to maniphest.edit, with only the client faked.

    Nothing between the options and Conduit is patched, so a path through
    run_edit that dropped --tag or --untag would send nothing and fail here.
    """

    @pytest.fixture
    def phab(self, monkeypatch):
        monkeypatch.setenv("PHAB_TOKEN", "cli-testtoken1234567890123456789")
        monkeypatch.setenv("PHAB_URL", "https://phabricator.example.com/api/")
        phab = _phab()
        phab.maniphest.search.return_value = _Result(data=[_task(["PHID-PROJ-qa"])])
        phab.maniphest.edit.return_value = {"object": {"id": 42}}
        monkeypatch.setattr("phabfive.core.Phabricator", lambda **kwargs: phab)
        return phab

    @pytest.mark.parametrize("app", [maniphest_app, main_app])
    @pytest.mark.parametrize(
        "option, transaction",
        [
            ("--tag=backend", {"type": "projects.add", "value": ["PHID-PROJ-backend"]}),
            (
                "--add-tag=#backend",
                {"type": "projects.add", "value": ["PHID-PROJ-backend"]},
            ),
            ("--untag=qa", {"type": "projects.remove", "value": ["PHID-PROJ-qa"]}),
            ("--remove-tag=2", {"type": "projects.remove", "value": ["PHID-PROJ-qa"]}),
        ],
    )
    def test_the_option_alone_reaches_conduit(self, phab, app, option, transaction):
        with patch("phabfive.cli.editor.edit_text") as editor:
            result = runner.invoke(app, ["edit", "T42", option])

        assert result.exit_code == 0, result.output
        editor.assert_not_called()
        [call] = phab.maniphest.edit.call_args_list
        assert call.kwargs["transactions"] == [transaction]

    @pytest.mark.parametrize("app", [maniphest_app, main_app])
    def test_adding_and_removing_one_project_sends_nothing(self, phab, app):
        result = runner.invoke(
            app, ["edit", "T42", "--add-tag=qa", "--remove-tag=#quality"]
        )

        assert result.exit_code != 0
        assert "Cannot both add and remove" in result.output
        phab.maniphest.edit.assert_not_called()
