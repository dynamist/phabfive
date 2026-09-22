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

    @pytest.mark.parametrize("option", ["--tag", "--subscribe"])
    def test_a_plus_still_splits_but_warns(self, option):
        result, instance = _create_task(f"{option}=a+b", f"{option}=c")

        assert result.exit_code == 0
        key = "tags" if option == "--tag" else "subscribers"
        assert instance.create_task.call_args.kwargs[key] == ["a", "b", "c"]
        assert f"WARNING: '+' between {option} values is deprecated" in result.output

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
            patch.object(maniphest, "_resolve_user_phid", return_value="PHID-USER-1"),
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
    instance.phab.user.search.return_value = {"data": []}
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
