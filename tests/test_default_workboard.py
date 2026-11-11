# -*- coding: utf-8 -*-

"""A repository's workboard, from `phabfive.tags.workboard` in .arcconfig.

The command scopes `maniphest search` and `stats` to it, and tags what
`maniphest create` makes with it, whenever the command line names no --tag.
"""

import json
import os
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.core import Phabfive

runner = CliRunner()


def _repo(tmp_path, arcconfig, git_is_file=False):
    """A checkout at tmp_path with `arcconfig` as its .arcconfig."""
    if git_is_file:
        # What a linked worktree or a submodule has
        (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    else:
        (tmp_path / ".git").mkdir()
    (tmp_path / ".arcconfig").write_text(json.dumps(arcconfig))
    return tmp_path


class TestArcconfig:
    def test_the_workboard_is_read(self, tmp_path):
        root = _repo(
            tmp_path,
            {
                "phabricator.uri": "https://phorge.example.com/",
                "phabfive": {"tags": {"workboard": "dynatron"}},
            },
        )

        with mock.patch("os.getcwd", return_value=str(root)):
            result = Phabfive._load_arcconfig()

        assert result == {
            "PHORGE_HOST": "https://phorge.example.com",
            "PHORGE_WORKBOARD": "dynatron",
        }

    def test_a_workboard_without_a_host_is_still_read(self, tmp_path):
        root = _repo(tmp_path, {"phabfive": {"tags": {"workboard": "#textarion"}}})

        with mock.patch("os.getcwd", return_value=str(root)):
            assert Phabfive._load_arcconfig() == {"PHORGE_WORKBOARD": "#textarion"}

    def test_hints_are_accepted_and_change_nothing(self, tmp_path, caplog):
        root = _repo(
            tmp_path,
            {
                "phabfive": {
                    "tags": {
                        "workboard": "dynatron",
                        "hints": {"security-risk": "auth changes", "textarion": None},
                    }
                }
            },
        )

        with mock.patch("os.getcwd", return_value=str(root)):
            assert Phabfive._load_arcconfig() == {"PHORGE_WORKBOARD": "dynatron"}
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    @pytest.mark.parametrize("value", [42, ["dynatron"], "", "  "])
    def test_a_workboard_that_is_not_a_name_is_ignored(self, tmp_path, value):
        root = _repo(tmp_path, {"phabfive": {"tags": {"workboard": value}}})

        with mock.patch("os.getcwd", return_value=str(root)):
            assert Phabfive._load_arcconfig() == {}

    @pytest.mark.parametrize(
        "block, named",
        [
            ("dynatron", "phabfive in"),
            ({"tag": {"workboard": "x"}}, "phabfive.tag "),
            ({"tags": ["x"]}, "phabfive.tags in"),
            ({"tags": {"workbord": "x"}}, "phabfive.tags.workbord"),
            ({"tags": {"hints": ["textarion"]}}, "phabfive.tags.hints in"),
            ({"tags": {"hints": {"textarion": 1}}}, "phabfive.tags.hints.textarion"),
        ],
    )
    def test_what_phabfive_does_not_know_is_named(self, tmp_path, caplog, block, named):
        root = _repo(tmp_path, {"phabfive": block})

        with mock.patch("os.getcwd", return_value=str(root)):
            assert Phabfive._load_arcconfig() == {}
        assert named in caplog.text

    def test_each_problem_is_warned_about_once(self, tmp_path, caplog):
        root = _repo(tmp_path, {"phabfive": {"tags": {"workbord": "x"}}})

        with mock.patch("os.getcwd", return_value=str(root)):
            Phabfive._load_arcconfig()
            Phabfive._load_arcconfig()

        assert caplog.text.count("phabfive.tags.workbord") == 1

    def test_a_malformed_block_keeps_the_host(self, tmp_path):
        root = _repo(
            tmp_path,
            {"phabricator.uri": "https://phorge.example.com/", "phabfive": 1},
        )

        with mock.patch("os.getcwd", return_value=str(root)):
            assert Phabfive._load_arcconfig() == {
                "PHORGE_HOST": "https://phorge.example.com"
            }

    def test_a_worktree_finds_its_arcconfig(self, tmp_path):
        root = _repo(
            tmp_path,
            {"phabricator.uri": "https://phorge.example.com/"},
            git_is_file=True,
        )
        subdir = root / "src"
        subdir.mkdir()

        with mock.patch("os.getcwd", return_value=str(subdir)):
            result = Phabfive._load_arcconfig()

        assert result == {"PHORGE_HOST": "https://phorge.example.com"}

    def test_the_environment_wins_and_empty_turns_it_off(self, tmp_path):
        root = _repo(tmp_path, {"phabfive": {"tags": {"workboard": "dynatron"}}})
        environ = {
            "PHORGE_HOST": "https://phorge.example.com",
            "PHORGE_TOKEN": "api-" + "a" * 28,
        }

        def read(extra):
            with (
                mock.patch("os.getcwd", return_value=str(root)),
                mock.patch.object(
                    os.path, "expanduser", return_value=str(tmp_path / "no-arcrc")
                ),
                mock.patch(
                    "phabfive.core.Phabfive._site_config_base",
                    return_value=str(tmp_path / "site"),
                ),
                mock.patch(
                    "appdirs.user_config_dir", return_value=str(tmp_path / "user")
                ),
                mock.patch.dict(os.environ, {**environ, **extra}, clear=True),
            ):
                conf, _ = Phabfive.read_config()
            return conf["PHORGE_WORKBOARD"]

        assert read({}) == "dynatron"
        assert read({"PHORGE_WORKBOARD": "textarion"}) == "textarion"
        assert read({"PHORGE_WORKBOARD": ""}) == ""


def _maniphest(project="dynatron"):
    instance = MagicMock()
    instance.conf = {"PHORGE_WORKBOARD": project}
    instance.task_search.return_value = {"tasks": [], "project_names": {}}
    instance.create_task.return_value = {"id": 1, "uri": "http://x/T1"}
    return instance


def _invoke(instance, *args):
    with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=instance):
        return runner.invoke(app, ["maniphest", *args])


def _searched_tag(instance):
    return instance.task_search.call_args.kwargs.get("tag")


class TestSearch:
    def test_a_bare_search_lists_the_project(self):
        instance = _maniphest()

        result = _invoke(instance, "search")

        assert result.exit_code == 0, result.output
        assert _searched_tag(instance) == "dynatron"
        assert "Searching workboard dynatron" in result.output

    def test_an_explicit_tag_replaces_it(self):
        instance = _maniphest()

        result = _invoke(instance, "search", "--tag=textarion")

        assert result.exit_code == 0, result.output
        assert _searched_tag(instance) == "textarion"
        assert "Searching workboard" not in result.output

    @pytest.mark.parametrize("option", ["--ids=T1", "--phids=PHID-TASK-x"])
    def test_tasks_asked_for_by_id_are_found_anywhere(self, option):
        instance = _maniphest()

        result = _invoke(instance, "search", option)

        assert result.exit_code == 0, result.output
        assert not _searched_tag(instance)

    def test_no_project_is_the_search_it_always_was(self):
        instance = _maniphest(project="")

        result = _invoke(instance, "search")

        # Still the help, rather than a search of the whole instance
        assert not instance.task_search.called
        assert "Searching workboard" not in result.output


class TestStats:
    def test_stats_counts_the_project(self):
        instance = _maniphest()
        seen = {}

        def plan_item(app, item, overrides):
            seen.update(overrides)
            raise SystemExit(0)

        with patch("phabfive.search.plan_item", side_effect=plan_item):
            result = _invoke(instance, "stats", "--since=2026-01-01")

        assert seen["tag"] == "dynatron"
        assert "Counting workboard dynatron" in result.output

    def test_an_explicit_tag_replaces_it(self):
        instance = _maniphest()
        seen = {}

        def plan_item(app, item, overrides):
            seen.update(overrides)
            raise SystemExit(0)

        with patch("phabfive.search.plan_item", side_effect=plan_item):
            _invoke(instance, "stats", "--since=2026-01-01", "--tag=textarion")

        assert seen["tag"] == "textarion"


class TestCreate:
    def test_a_new_task_lands_on_the_board(self):
        instance = _maniphest()

        result = _invoke(instance, "create", "A task", "--description=x")

        assert result.exit_code == 0, result.output
        assert instance.create_task.call_args.kwargs["tags"] == ["dynatron"]
        assert "Tagging with workboard dynatron" in result.output

    def test_an_explicit_tag_replaces_it(self):
        instance = _maniphest()

        result = _invoke(instance, "create", "A task", "--description=x", "--tag=qa")

        assert result.exit_code == 0, result.output
        assert instance.create_task.call_args.kwargs["tags"] == ["qa"]

    def test_the_project_is_the_board_for_a_column(self):
        instance = _maniphest()
        instance._resolve_project_phids.return_value = ["PHID-PROJ-dynatron"]

        result = _invoke(
            instance, "create", "A task", "--description=x", "--column=Backlog"
        )

        assert result.exit_code == 0, result.output
        instance._resolve_project_phids.assert_called_with("dynatron")


class TestEdit:
    def test_edit_never_tags_with_it(self):
        instance = _maniphest()

        with patch("phabfive.cli.maniphest._get_edit_app") as get_edit:
            _invoke(instance, "edit", "T1", "--priority=high")

        kwargs = get_edit.return_value.plan.call_args
        assert "dynatron" not in repr(kwargs)
