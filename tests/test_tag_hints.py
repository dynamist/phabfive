# -*- coding: utf-8 -*-

"""A repository's tag hints, from `phabfive.tags.hints` in .arcconfig.

`phabfive hints` shows them with the workboard, and `maniphest create`
reminds of the ones a new task was not given. Nothing ever adds a hinted tag.
"""

import json
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.cli.hints import repository_hints, unmet_hints
from phabfive.core import Phabfive, RepositoryConfig
from phabfive.exceptions import PhabfiveAPIException
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.resolvers import resolve_tag_hints

runner = CliRunner()

HOST = "https://phorge.example.com"


def _repo(tmp_path, arcconfig):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".arcconfig").write_text(json.dumps(arcconfig))
    return tmp_path


def _read(tmp_path, arcconfig):
    root = _repo(tmp_path, arcconfig)
    with mock.patch("os.getcwd", return_value=str(root)):
        return Phabfive.repository_config()


class TestRepositoryConfig:
    def test_hints_are_kept_in_the_order_written(self, tmp_path):
        repository = _read(
            tmp_path,
            {
                "phabricator.uri": f"{HOST}/",
                "phabfive": {
                    "tags": {
                        "workboard": "dynatron",
                        "hints": {
                            "security-risk": "  Who can log in  ",
                            "textarion": None,
                            "policy": "",
                        },
                    }
                },
            },
        )

        assert repository.host == HOST
        assert repository.workboard == "dynatron"
        assert list(repository.hints) == ["security-risk", "textarion", "policy"]
        assert repository.hints["security-risk"] == "Who can log in"
        # An empty note says no more than null
        assert repository.hints["textarion"] is None
        assert repository.hints["policy"] is None

    def test_a_malformed_hint_is_left_out_and_named(self, tmp_path, caplog):
        repository = _read(
            tmp_path,
            {"phabfive": {"tags": {"hints": {"good": "yes", "bad": 3, " ": "x"}}}},
        )

        assert dict(repository.hints) == {"good": "yes"}
        assert "phabfive.tags.hints.bad" in caplog.text
        assert "an empty tag" in caplog.text

    def test_hints_cannot_be_changed_through_the_record(self, tmp_path):
        repository = _read(tmp_path, {"phabfive": {"tags": {"hints": {"a": None}}}})

        with pytest.raises(TypeError):
            repository.hints["b"] = None

    def test_no_arcconfig_is_no_repository(self, tmp_path):
        (tmp_path / ".git").mkdir()

        with mock.patch("os.getcwd", return_value=str(tmp_path)):
            assert Phabfive.repository_config() is None

    def test_explicit_configuration_discovers_no_repository(self):
        phabfive = Phabfive(url=HOST, token="api-" + "x" * 28)

        assert phabfive.repository is None


PROJECTS = {
    "PHID-PROJ-sec": {"name": "Security Risk", "slugs": ["security_risk"]},
    "PHID-PROJ-dyn": {"name": "Dynatron", "slugs": ["dynatron"]},
    "PHID-PROJ-s1a": {"name": "Sprint 1", "slugs": []},
    "PHID-PROJ-s1b": {"name": "Sprint 1", "slugs": []},
}
IDS = {"PHID-PROJ-sec": 7, "PHID-PROJ-dyn": 8, "PHID-PROJ-s1a": 9, "PHID-PROJ-s1b": 10}
DESCRIPTIONS = {"PHID-PROJ-sec": "Risk to unauthorized access."}


def _project_search(constraints):
    phids = constraints.get("phids") or [
        phid for phid, pid in IDS.items() if pid in constraints.get("ids", [])
    ]
    return {
        "data": [
            {
                "id": IDS[phid],
                "phid": phid,
                "fields": {
                    "name": PROJECTS[phid]["name"],
                    "description": DESCRIPTIONS.get(phid, ""),
                },
            }
            for phid in phids
            if phid in PROJECTS
        ]
    }


def _phab():
    phab = MagicMock()
    phab.project.query.return_value = {"data": PROJECTS}
    phab.project.search.side_effect = lambda constraints: _project_search(constraints)
    return phab


class TestResolveTagHints:
    def test_each_tag_is_found_or_says_why_not(self):
        found = resolve_tag_hints(
            _phab(), ["Security_Risk", "8", "Sprint 1", "nothing", "sec*"]
        )

        assert list(found) == ["Security_Risk", "8", "Sprint 1", "nothing", "sec*"]
        assert found["Security_Risk"]["name"] == "Security Risk"
        assert found["Security_Risk"]["description"] == "Risk to unauthorized access."
        assert found["Security_Risk"]["problem"] is None
        assert found["8"]["phid"] == "PHID-PROJ-dyn"
        # An empty description is no description
        assert found["8"]["description"] is None
        assert "Sprint 1" in found["Sprint 1"]["problem"]
        assert found["Sprint 1"]["phid"] is None
        assert found["nothing"]["problem"].startswith("No project")
        assert "wildcards" in found["sec*"]["problem"]

    def test_an_unreachable_instance_fails_rather_than_looking_stale(self):
        phab = _phab()
        phab.project.search.side_effect = PhabfiveAPIException("ERR-CONDUIT", "down")

        with pytest.raises(PhabfiveAPIException):
            resolve_tag_hints(phab, ["security_risk"])


def _maniphest():
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = _phab()
    return maniphest


class TestTagHints:
    def test_the_workboard_comes_first_with_the_columns_it_shows(self):
        columns = [
            {"id": 1, "fields": {"name": "Backlog", "isHidden": False}},
            {"id": 2, "fields": {"name": "Archive", "isHidden": True}},
            {"id": 3, "fields": {"name": "Done", "isHidden": False}},
        ]
        with patch(
            "phabfive.project.fetchers.fetch_boards_columns",
            return_value=({"PHID-PROJ-dyn": columns}, {}),
        ):
            found = _maniphest().tag_hints(
                {"security_risk": "Who can log in"}, workboard="dynatron"
            )

        assert [(f["tag"], f["role"]) for f in found] == [
            ("dynatron", "workboard"),
            ("security_risk", "hint"),
        ]
        assert found[0]["columns"] == ["Backlog", "Done"]
        assert found[1]["note"] == "Who can log in"
        assert "columns" not in found[1]

    def test_nothing_to_show_asks_nothing(self):
        maniphest = _maniphest()

        assert maniphest.tag_hints({}) == []
        maniphest.phab.project.query.assert_not_called()


class TestRepositoryHints:
    def _app(self, host=HOST, repository_host=HOST):
        instance = MagicMock()
        instance.conf = {"PHORGE_HOST": host}
        instance.repository = RepositoryConfig(
            path="/src/.arcconfig",
            host=repository_host,
            hints={"security-risk": "note"},
        )
        return instance

    def test_the_repository_hints_are_used_on_its_instance(self):
        assert repository_hints(self._app()) == {"security-risk": "note"}

    def test_another_instance_gets_none(self):
        app = self._app(host="https://other.example.com")

        assert repository_hints(app) == {}

    def test_a_repository_that_names_no_instance_still_hints(self):
        assert repository_hints(self._app(repository_host=None))

    def test_a_fake_app_has_no_repository(self):
        assert repository_hints(MagicMock()) == {}


class TestUnmetHints:
    @pytest.mark.parametrize(
        "given", ["security-risk", "#Security_Risk", "Security Risk", "SECURITY-RISK"]
    )
    def test_spellings_of_one_tag_meet_it(self, given):
        assert unmet_hints({"security-risk": None}, [[given]]) == []

    def test_a_hint_any_task_lacks_is_unmet(self):
        hints = {"security-risk": None, "policy": None}

        assert unmet_hints(hints, [["security-risk", "policy"], ["policy"]]) == [
            "security-risk"
        ]


def _cli_maniphest(hints=None, workboard="dynatron", repository_host=HOST):
    instance = MagicMock()
    instance.conf = {"PHORGE_HOST": HOST, "PHORGE_WORKBOARD": workboard}
    instance.url = HOST
    instance.has_explicit_host.return_value = True
    instance.format_link.side_effect = lambda url, text, show_url=True: text
    instance.repository = RepositoryConfig(
        path="/src/.arcconfig",
        host=repository_host,
        workboard=workboard or None,
        hints=hints or {},
    )
    instance.create_task.return_value = {"id": 1, "uri": f"{HOST}/T1"}
    return instance


def _invoke(instance, *args, format=None):
    options = [f"--format={format}"] if format else []
    with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=instance):
        return runner.invoke(app, [*options, "maniphest", *args])


WORKBOARD = {
    "tag": "dynatron",
    "role": "workboard",
    "note": None,
    "phid": "PHID-PROJ-dyn",
    "id": 8,
    "name": "Dynatron",
    "description": "How the board is used",
    "problem": None,
    "columns": ["Backlog", "Done"],
}
STALE = {
    "tag": "security-risk",
    "role": "hint",
    "note": "Who can log in",
    "phid": None,
    "id": None,
    "name": None,
    "description": None,
    "problem": "No project has this name, hashtag, ID or PHID",
}


def _hints(instance=None, error=None, hosts=(), repository=None):
    """Run `phabfive --format=json hints` and parse its one record."""
    from phabfive.exceptions import PhabfiveConfigException

    new_app = MagicMock(
        return_value=instance,
        side_effect=PhabfiveConfigException(error) if error else None,
    )
    if repository is None and instance is not None:
        repository = instance.repository
    with (
        patch("phabfive.cli.apps.new_app", new_app),
        patch.object(Phabfive, "repository_config", return_value=repository),
        patch.object(Phabfive, "arcrc_hosts", return_value=list(hosts)),
    ):
        result = runner.invoke(app, ["--format=json", "hints"])

    assert result.exit_code == 0, result.output
    [record] = json.loads(result.stdout)
    return record, result, new_app


class TestHintsCommand:
    def test_in_a_repository_everything_it_names_is_shown(self, monkeypatch):
        monkeypatch.delenv("PHORGE_HOST", raising=False)
        monkeypatch.delenv("PHORGE_API_HOST", raising=False)
        instance = _cli_maniphest(hints={"security-risk": "Who can log in"})
        instance.tag_hints.return_value = [WORKBOARD, STALE]

        record, result, new_app = _hints(instance)

        # Reports the configuration: never connects, never prompts
        assert new_app.call_args.kwargs == {"verify": False, "select_host": None}
        instance.tag_hints.assert_called_once_with(
            {"security-risk": "Who can log in"}, workboard="dynatron"
        )
        assert record["Instance"] == {"URL": HOST, "From": ".arcconfig"}
        assert "Instances" not in record
        assert record["Repository"] == {"Config": "/src/.arcconfig", "Instance": HOST}
        assert record["Workboard"] == {
            "Link": f"{HOST}/project/view/8/",
            "Tag": "dynatron",
            "Project": "Dynatron",
            "Description": "How the board is used",
            "Columns": ["Backlog", "Done"],
            "From": ".arcconfig",
        }
        [stale] = record["Tags"]
        assert stale["Problem"].startswith("No project")
        assert stale["Note"] == "Who can log in"
        assert "Link" not in stale
        assert "Warning: security-risk" in result.stderr

    def test_a_repository_on_another_instance_says_why_its_hints_are_left_out(self):
        instance = _cli_maniphest(
            hints={"security-risk": None}, repository_host="https://other.example.com"
        )
        instance.tag_hints.return_value = [WORKBOARD]

        record, _, _ = _hints(instance)

        assert "left out" in record["Repository"]["Problem"]
        assert "Tags" not in record
        instance.tag_hints.assert_called_once_with({}, workboard="dynatron")

    def test_a_repository_with_nothing_to_look_up_asks_nothing(self):
        instance = _cli_maniphest(workboard="")

        record, _, _ = _hints(instance)

        assert "Workboard" not in record and "Tags" not in record
        instance.tag_hints.assert_not_called()

    def test_outside_a_repository_every_instance_is_listed(self, monkeypatch):
        monkeypatch.setenv("PHORGE_HOST", HOST)
        instance = _cli_maniphest()
        instance.repository = None

        record, _, _ = _hints(instance, hosts=[HOST, "https://we.phorge.it"])

        assert record["Instance"] == {"URL": HOST, "From": "PHORGE_HOST"}
        assert record["Instances"] == [HOST, "https://we.phorge.it"]
        assert "Repository" not in record

    def test_several_hosts_and_none_chosen_is_an_answer(self):
        record, _, _ = _hints(
            error="Multiple hosts found in ~/.arcrc",
            hosts=[HOST, "https://we.phorge.it"],
        )

        assert record["Instance"]["URL"] is None
        assert record["Instance"]["From"].startswith("none chosen")
        assert "Problem" not in record["Instance"]
        assert record["Instances"] == [HOST, "https://we.phorge.it"]

    def test_no_configuration_says_how_to_make_one(self):
        record, _, _ = _hints(error="PHORGE_HOST is not configured")

        assert record["Instance"]["From"].startswith("none configured")
        assert record["Instance"]["Problem"] == "PHORGE_HOST is not configured"
        assert record["Instances"] == []

    def test_a_named_host_is_kept_when_its_configuration_fails(self, monkeypatch):
        monkeypatch.delenv("PHORGE_HOST", raising=False)
        monkeypatch.delenv("PHORGE_API_HOST", raising=False)
        repository = RepositoryConfig(path="/src/.arcconfig", host=HOST)

        record, _, _ = _hints(
            error="No token for this host in ~/.arcrc",
            hosts=["https://we.phorge.it", "https://other.example.com"],
            repository=repository,
        )

        # Not "none chosen": the repository chose, and its token is missing
        assert record["Instance"] == {
            "URL": HOST,
            "From": ".arcconfig",
            "Problem": "No token for this host in ~/.arcrc",
        }

    def test_a_workboard_from_the_environment_is_shown_anywhere(self, monkeypatch):
        monkeypatch.setenv("PHORGE_WORKBOARD", "dynatron")
        instance = _cli_maniphest()
        instance.repository = None
        instance.tag_hints.return_value = [WORKBOARD]

        record, _, _ = _hints(instance)

        assert record["Workboard"]["From"] == "PHORGE_WORKBOARD"
        assert "Repository" not in record
        instance.tag_hints.assert_called_once_with({}, workboard="dynatron")


def _home(monkeypatch, path):
    """Make `~` mean `path`: HOME on POSIX, USERPROFILE on Windows."""
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


class TestArcrcHosts:
    def test_the_hosts_are_listed_by_their_root(self, tmp_path, monkeypatch):
        _home(monkeypatch, tmp_path)
        (tmp_path / ".arcrc").write_text(
            json.dumps(
                {
                    "hosts": {
                        f"{HOST}/api/": {"token": "api-secret"},
                        "https://we.phorge.it/api/": {"token": "api-secret"},
                    }
                }
            )
        )

        assert Phabfive.arcrc_hosts() == [HOST, "https://we.phorge.it"]

    def test_no_file_no_hosts(self, tmp_path, monkeypatch):
        _home(monkeypatch, tmp_path)

        assert Phabfive.arcrc_hosts() == []


class TestCreateReminder:
    def test_a_hinted_tag_the_task_lacks_is_named(self):
        instance = _cli_maniphest(hints={"security-risk": "note", "policy": None})

        result = _invoke(
            instance, "create", "A task", "--description=x", "--tag=Policy"
        )

        assert result.exit_code == 0, result.output
        assert (
            "Not tagged: security-risk (repository hints; "
            "see phabfive hints)" in result.stderr
        )
        # A reminder, never a tag
        assert instance.create_task.call_args.kwargs["tags"] == ["Policy"]

    def test_a_task_with_every_hinted_tag_hears_nothing(self):
        instance = _cli_maniphest(hints={"security-risk": None})

        result = _invoke(
            instance, "create", "A task", "--description=x", "--tag=#Security_Risk"
        )

        assert "Not tagged" not in result.stderr

    def test_no_hints_no_reminder(self):
        result = _invoke(_cli_maniphest(), "create", "A task", "--description=x")

        assert "Not tagged" not in result.stderr

    def test_a_spec_is_reminded_of_what_any_task_lacks(self, tmp_path):
        from phabfive.spec.create import CreateItem, CreatePlan

        instance = _cli_maniphest(hints={"security-risk": None, "policy": None})
        plan = CreatePlan(
            items=(
                CreateItem("task", "tasks[0]", display={"projects": ["Policy"]}),
                CreateItem(
                    "task",
                    "tasks[1]",
                    display={"projects": ["Policy", "Security Risk"]},
                ),
                CreateItem("task", "tasks[2]", anchor=True),
            )
        )
        spec = tmp_path / "spec.yaml"
        spec.write_text("tasks: []\n")

        with (
            patch("phabfive.cli.create_spec.load_create_spec"),
            patch("phabfive.create.plan_spec", return_value=plan),
            patch("phabfive.cli.spec_run._preview"),
        ):
            result = _invoke(instance, "create", f"--with={spec}", "--dry-run")

        assert result.exit_code == 0, result.output
        assert "Not tagged on every task: security-risk " in result.stderr

    def test_apply_is_reminded_too(self, tmp_path):
        from phabfive.spec.create import CreateItem, CreatePlan

        instance = _cli_maniphest(hints={"security-risk": None})
        plan = CreatePlan(
            items=(CreateItem("task", "tasks[0]", display={"projects": ["QA"]}),)
        )
        spec = tmp_path / "spec.yaml"
        spec.write_text("tasks: []\n")

        with (
            patch("phabfive.cli.spec_run._loaded"),
            patch("phabfive.cli.spec_run._checked"),
            patch("phabfive.cli.spec_run._instance", return_value=instance),
            patch("phabfive.create.plan_spec", return_value=plan),
            patch("phabfive.cli.spec_run._preview"),
        ):
            result = runner.invoke(app, ["apply", "-f", str(spec), "--dry-run"])

        assert "Not tagged: security-risk " in result.stderr
