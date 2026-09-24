# -*- coding: utf-8 -*-

"""Every list option that adds values splits them the same way (#429).

`maniphest create --tag/--subscribe` split on `+` alone, and `paste
create/edit --tag/--subscribe` did not split at all, so `--subscribe=@a,@b`
meant two people on `maniphest edit`, one person named `@a,@b` on `maniphest
create`, and a project named `a,b` on `paste create`. They all go through
`split_list_option` now. `+` still works on `maniphest create`, with a
warning.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.maniphest import maniphest_app
from phabfive.cli.paste import paste_app
from phabfive.maniphest import Maniphest

runner = CliRunner()


def _a_maniphest():
    instance = MagicMock()
    instance.create_task.return_value = {"dry_run": True, "title": "probe"}
    instance._resolve_project_phids.return_value = ["PHID-PROJ-board"]
    return instance


def _create_task(*args):
    instance = _a_maniphest()
    with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=instance):
        result = runner.invoke(
            maniphest_app,
            ["create", "probe", "--description=x", "--dry-run", *args],
        )
    return result, instance


class TestManiphestCreate:
    def test_commas_split_tags_and_subscribers(self):
        result, instance = _create_task(
            "--tag=Backend,QA", "--tag=Ops", "--subscribe=@a,@b"
        )

        assert result.exit_code == 0
        kwargs = instance.create_task.call_args.kwargs
        assert kwargs["tags"] == ["Backend", "QA", "Ops"]
        assert kwargs["subscribers"] == ["@a", "@b"]
        assert "deprecated" not in result.output

    @pytest.mark.parametrize(
        "option, named",
        [
            ("--tag", "--tag"),
            ("--subscribe", "--subscribe"),
            # The hidden alias is warned about by the name --help shows
            ("--add-subscriber", "--subscribe"),
        ],
    )
    def test_a_plus_still_splits_but_warns(self, option, named):
        result, instance = _create_task(f"{option}=a+b", f"{option}=c")

        assert result.exit_code == 0
        key = "tags" if option == "--tag" else "subscribers"
        assert instance.create_task.call_args.kwargs[key] == ["a", "b", "c"]
        assert f"WARNING: '+' between {named} values is deprecated" in result.output

    def test_subscribe_and_its_alias_are_one_list(self):
        result, instance = _create_task("--subscribe=@a", "--add-subscriber=@b,@a")

        assert result.exit_code == 0
        assert instance.create_task.call_args.kwargs["subscribers"] == ["@a", "@b"]

    def test_the_board_is_the_first_tag_once_split(self):
        """`--column` reads its board from the first tag, not the first
        occurrence of the option, which may hold several."""
        result, instance = _create_task("--tag=Board,Other", "--column=Backlog")

        assert result.exit_code == 0
        instance._resolve_project_phids.assert_called_once_with("Board")

    def test_an_empty_tag_does_not_satisfy_column(self):
        result, instance = _create_task("--tag=", "--column=Backlog")

        assert result.exit_code == 1
        assert "--column requires --tag" in result.output
        instance.create_task.assert_not_called()


class TestCreateTaskLibrary:
    """`Maniphest.create_task` reads a list the same way the CLI does."""

    @pytest.fixture
    def maniphest(self):
        with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
            maniphest = Maniphest()
        maniphest.phab = MagicMock()
        maniphest.url = "http://phorge.localhost"
        return maniphest

    def test_commas_split_and_plus_does_not(self, maniphest):
        with (
            patch.object(
                maniphest,
                "_resolve_project_phids_for_create",
                return_value={"phids": ["PHID-PROJ-1"], "slugs": ["c"]},
            ) as resolve_projects,
            patch.object(
                maniphest,
                "_resolve_users",
                side_effect=lambda values, option=None: {
                    v: (f"PHID-USER-{v}", v) for v in values
                },
            ),
        ):
            result = maniphest.create_task(
                "A task",
                tags=["a, b", "C++", "a"],
                subscribers="alice,bob",
                dry_run=True,
            )

        resolve_projects.assert_called_once_with(["a", "b", "C++"])
        assert result["tags"] == ["a", "b", "C++"]
        assert result["subscribers"] == ["alice", "bob"]


def _a_paste():
    instance = MagicMock()
    instance.create_paste_from_content.return_value = {"id": 42}
    instance.get_paste_url.return_value = "https://example.com/P42"
    instance.get_paste_for_edit.return_value = {
        "id": 42,
        "phid": "PHID-PSTE-42",
        "title": "Notes",
        "content": "hello",
        "language": "text",
    }
    instance.edit_paste.return_value = {"success": True, "changes": []}
    instance.phab.user.whoami.return_value = {
        "phid": "PHID-USER-caller",
        "userName": "caller",
    }
    users = {"caller": "PHID-USER-caller", "alice": "PHID-USER-a", "bob": "PHID-USER-b"}
    instance.phab.user.search.side_effect = lambda constraints: {
        "data": [
            {"phid": phid, "fields": {"username": name}}
            for name, phid in users.items()
            if name in constraints.get("usernames", [])
        ]
    }
    return instance


class TestPaste:
    def test_create_splits_tags_and_subscribers(self):
        instance = _a_paste()
        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app,
                [
                    "create",
                    "Notes",
                    "--content=hello",
                    "--tag=a,b",
                    "--tag=c",
                    "--subscribe=@me,bob",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = instance.create_paste_from_content.call_args.kwargs
        assert kwargs["tags"] == ["a", "b", "c"]
        assert kwargs["subscribers"] == ["caller", "bob"]

    def test_edit_splits_tags_and_subscribers(self):
        instance = _a_paste()
        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app,
                ["edit", "P42", "--tag=a, b", "--subscribe=alice,bob,alice"],
            )

        assert result.exit_code == 0, result.output
        kwargs = instance.edit_paste.call_args.kwargs
        assert kwargs["tags"] == ["a", "b"]
        assert kwargs["subscribers"] == ["alice", "bob"]

    def test_edit_splits_subscribers_to_remove_and_merges_the_aliases(self):
        instance = _a_paste()
        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app,
                [
                    "edit",
                    "P42",
                    "--subscribe=alice",
                    "--add-subscriber=bob",
                    "--unsubscribe=carol,dave",
                    "--remove-subscriber=erin",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = instance.edit_paste.call_args.kwargs
        assert kwargs["subscribers"] == ["alice", "bob"]
        assert kwargs["unsubscribers"] == ["carol", "dave", "erin"]


class TestSubscriberAliases:
    """`--add-subscriber` and `--remove-subscriber` are hidden spellings of
    `--subscribe` and `--unsubscribe`, merged into them.

    Hidden means left out of --help and of option-name completion (click
    skips hidden options there), which is the point: one name to find."""

    @pytest.mark.parametrize(
        "app, command",
        [
            (maniphest_app, "edit"),
            (paste_app, "edit"),
        ],
    )
    def test_help_shows_only_the_primary_names(self, app, command):
        result = runner.invoke(app, [command, "--help"], env={"COLUMNS": "200"})

        assert "--subscribe " in result.output
        assert "--unsubscribe" in result.output
        assert "--add-subscriber" not in result.output
        assert "--remove-subscriber" not in result.output

    def test_maniphest_edit_merges_the_aliases(self):
        with (
            patch("phabfive.cli.maniphest._get_edit_app"),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(
                maniphest_app,
                [
                    "edit",
                    "T1",
                    "--subscribe=a",
                    "--add-subscriber=b",
                    "--unsubscribe=c",
                    "--remove-subscriber=d",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = run_edit.call_args.kwargs
        assert kwargs["subscribe"] == ["a", "b"]
        assert kwargs["unsubscribe"] == ["c", "d"]


class TestMemberAliases:
    """`project edit --join/--leave`, with `--add-member/--remove-member` hidden."""

    def test_help_shows_only_the_primary_names(self):
        from phabfive.cli.project import project_app

        result = runner.invoke(project_app, ["edit", "--help"], env={"COLUMNS": "200"})

        assert "--join " in result.output
        assert "--leave" in result.output
        assert "--add-member" not in result.output
        assert "--remove-member" not in result.output

    def test_the_aliases_are_merged(self):
        from phabfive.cli.project import project_app

        instance = MagicMock()
        instance.get_project_for_edit.return_value = {
            "id": 1,
            "fields": {"name": "Platform"},
        }
        instance.build_project_edit.return_value = ([], [])
        with patch("phabfive.cli.project._get_project_app", return_value=instance):
            result = runner.invoke(
                project_app,
                [
                    "edit",
                    "#platform",
                    "--join=a",
                    "--add-member=b,a",
                    "--leave=c",
                    "--remove-member=d",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = instance.build_project_edit.call_args.kwargs
        assert kwargs["add_members"] == ["a", "b"]
        assert kwargs["remove_members"] == ["c", "d"]
