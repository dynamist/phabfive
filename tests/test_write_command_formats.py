# -*- coding: utf-8 -*-
"""Tests for --format on the commands that write (#344).

A create, an edit or a comment used to print a URL or a human diff whatever
was asked for, so a caller passing --format=json got something it could not
parse, with exit code 0 and nothing on stderr to say the option was ignored.

Two rules are pinned here:

* A machine-readable format - yaml, json, jsonl - answers with the record
  ``show`` gives for the object that was written, and every line of prose
  moves to stderr. Nothing invents a second "result" shape, so the assertions
  below are about the same records ``maniphest show`` emits.
* A human format - rich, tree, table, value - prints exactly what it printed
  before. This change is additive for people and new for machines.

A dry run is the one case with no record to give, since nothing was written.
It puts its preview on stderr and leaves stdout empty, which is what
``maniphest search`` with no hits has always done.
"""

# python std lib
import json
import subprocess
import sys
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app
from phabfive.cli.output import is_machine_format
from phabfive.core import Phabfive

runner = CliRunner()

MACHINE = ["yaml", "json", "jsonl"]
HUMAN = ["rich", "tree", "table", "value"]


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


def a_task_record(task_id=123, name="probe", priority="Normal"):
    """One entry of what task_show() hands the renderers."""
    return {
        "_url": f"https://phorge.example.com/T{task_id}",
        "Task": {"Name": name, "Status": "Open", "Priority": priority},
    }


def a_maniphest(task_id=123, **record):
    """A Maniphest whose create, comment and show are all answered."""
    instance = MagicMock()
    instance.create_task.return_value = {
        "phid": "PHID-TASK-x",
        "id": task_id,
        "uri": f"https://phorge.example.com/T{task_id}",
        "tag_slugs": [],
        "base_url": "https://phorge.example.com",
    }
    instance.add_task_comment.return_value = {"phid": "PHID-TASK-x"}
    instance.get_task_info.return_value = {
        "uri": f"https://phorge.example.com/T{task_id}"
    }
    instance.task_show.return_value = {
        "tasks": [a_task_record(task_id, **record)],
        "missing_ids": [],
    }
    return instance


def a_paste_record(paste_id=42, title="notes", language="text", content="hello"):
    """One entry of what paste_show() hands the renderers."""
    return {
        "id": f"P{paste_id}",
        "url": f"https://phorge.example.com/P{paste_id}",
        "_link": f"https://phorge.example.com/P{paste_id}",
        "title": title,
        "author": "admin",
        "language": language,
        "status": "active",
        "content": content,
    }


def a_paste(paste_id=42, **record):
    """A Paste whose create, edit, comment and show are all answered."""
    instance = MagicMock()
    instance.create_paste_from_content.return_value = {
        "id": paste_id,
        "phid": "PHID-PSTE-x",
    }
    instance.get_paste_url.return_value = f"https://phorge.example.com/P{paste_id}"
    instance.get_paste_data.return_value = {
        "title": "notes",
        "content": "hello",
        "language": "text",
    }
    instance.edit_paste.return_value = {
        "paste_id": paste_id,
        "changes": [{"field": "Language", "new": "python"}],
    }
    instance.add_paste_comment.return_value = {"success": True, "paste_id": paste_id}
    instance.paste_show.return_value = {
        "pastes": [a_paste_record(paste_id, **record)],
        "missing_ids": [],
    }
    return instance


def a_repo_record(monogram="R7", name="probe", default_branch="master"):
    """One entry of what repo_show() hands the renderers."""
    return {
        "_url": f"https://phorge.example.com/source/{name}/",
        "Repository": {
            "Name": name,
            "Short Name": name,
            "Monogram": monogram,
            "Status": "active",
            "VCS": "git",
            "Default Branch": default_branch,
        },
    }


def a_uri_record(uri="git@example.com:org/repo.git", origin="external"):
    """One entry of what uri_list() hands the renderers.

    A URI carries no _url: it has no page of its own, which is why the
    renderers lead such a record with its first field instead of a Link.
    """
    return {"URI": uri, "Origin": origin, "Role": "Phorge pulls from here"}


def a_diffusion(monogram="R7", name="probe", uris=None):
    """A Diffusion whose create, edit and both reads are answered."""
    instance = MagicMock()
    instance.build_repo_create.return_value = (
        [{"type": "shortName", "value": name}],
        [{"field": "Short name", "old": None, "new": name}],
    )
    instance.apply_repo_create.return_value = f"PHID-REPO-{name}"
    instance.get_repo_record.return_value = {
        "id": int(monogram[1:]),
        "phid": f"PHID-REPO-{name}",
        "fields": {
            "name": name,
            "shortName": name,
            "status": "active",
            "isHosted": True,
        },
        "attachments": {"uris": {"uris": []}},
    }
    instance.link_repository.return_value = f"https://phorge.example.com/{monogram}"
    instance.build_repo_edit.return_value = (
        [{"type": "defaultBranch", "value": "main"}],
        [{"field": "Default branch", "old": "master", "new": "main"}],
    )
    instance.build_uri_create.return_value = (
        {"repository": name},
        [{"field": "URI", "old": None, "new": "git@example.com:org/repo.git"}],
    )
    instance.build_uri_edit.return_value = (
        [{"type": "display", "value": "never"}],
        [{"field": "Display", "old": "always", "new": "never"}],
    )
    instance.get_uri_and_repo.return_value = (
        instance.get_repo_record.return_value,
        {"id": 11, "phid": "PHID-RURI-x"},
    )
    instance.repo_show.return_value = {
        "repositories": [a_repo_record(monogram, name)],
        "missing_ids": [],
    }
    instance.uri_list.return_value = {
        "uris": uris if uris is not None else [a_uri_record()]
    }
    return instance


class TestIsMachineFormat:
    """The one predicate every write command branches on."""

    @pytest.mark.parametrize("output_format", MACHINE)
    def test_machine_formats(self, output_format):
        assert is_machine_format(output_format)

    @pytest.mark.parametrize("output_format", HUMAN)
    def test_human_formats(self, output_format):
        assert not is_machine_format(output_format)

    @pytest.mark.parametrize(
        "alias, resolved", [("strict", "yaml"), ("ndjson", "jsonl")]
    )
    def test_an_alias_answers_like_what_it_names(self, alias, resolved):
        # preprocess_format_alias normally rewrites these in argv long before
        # a command sees one, so this is the belt to that braces.
        assert is_machine_format(alias) is is_machine_format(resolved)

    def test_simple_is_not_machine_readable(self):
        # `value` is bare values for a shell pipeline, and a URL on its own
        # already is one - so create keeps printing the URL there.
        assert not is_machine_format("simple")

    def test_no_format_asked_for(self):
        assert not is_machine_format(None)


class TestManiphestCreate:
    """`maniphest create` answers with the new task's record."""

    @pytest.mark.parametrize("output_format", MACHINE)
    def test_emits_the_show_record(self, output_format):
        maniphest = a_maniphest(task_id=7, name="probe 344")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                [f"--format={output_format}", "maniphest", "create", "probe", "--yes"],
            )

        assert result.exit_code == 0
        assert "https://phorge.example.com/T7" in result.stdout
        assert "probe 344" in result.stdout
        # The record came from task_show, not from a shape invented here
        maniphest.task_show.assert_called_once_with([7])

    def test_json_stdout_parses_on_its_own(self):
        """The whole point: stdout alone, stderr discarded, is JSON."""
        maniphest = a_maniphest(task_id=7, name="probe 344")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["--format=json", "maniphest", "create", "probe", "--yes"]
            )

        [record] = json.loads(result.stdout)
        assert record["Link"] == "https://phorge.example.com/T7"
        assert record["Task"]["Name"] == "probe 344"

    def test_jsonl_agrees_with_json(self):
        """The standing rule: jsonl parsed line by line is the json array."""
        outputs = {}
        for output_format in ("json", "jsonl"):
            with patch(
                "phabfive.cli.maniphest._get_maniphest_app",
                return_value=a_maniphest(task_id=7),
            ):
                outputs[output_format] = runner.invoke(
                    app,
                    [
                        f"--format={output_format}",
                        "maniphest",
                        "create",
                        "probe",
                        "--yes",
                    ],
                ).stdout

        as_lines = [json.loads(line) for line in outputs["jsonl"].splitlines() if line]
        assert as_lines == json.loads(outputs["json"])

    @pytest.mark.parametrize("output_format", HUMAN)
    def test_a_human_format_still_prints_the_url_alone(self, output_format):
        maniphest = a_maniphest(task_id=7)

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                [f"--format={output_format}", "maniphest", "create", "probe", "--yes"],
            )

        assert result.exit_code == 0
        assert result.stdout.strip() == "https://phorge.example.com/T7"
        maniphest.task_show.assert_not_called()

    def test_dry_run_leaves_stdout_empty(self):
        maniphest = a_maniphest()
        maniphest.create_task.return_value = {
            "dry_run": True,
            "title": "probe",
            "priority": "High",
        }

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                ["--format=json", "maniphest", "create", "probe", "--dry-run", "--yes"],
            )

        assert result.exit_code == 0
        assert result.stdout.strip() == ""
        assert "[DRY RUN] Would create task:" in result.stderr
        assert "Priority: High" in result.stderr

    def test_a_human_dry_run_keeps_its_preview_on_stdout(self):
        maniphest = a_maniphest()
        maniphest.create_task.return_value = {"dry_run": True, "title": "probe"}

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                ["--format=rich", "maniphest", "create", "probe", "--dry-run", "--yes"],
            )

        assert "[DRY RUN] Would create task:" in result.stdout


class TestManiphestComment:
    """`maniphest comment` answers with the commented task's record."""

    def test_emits_the_show_record(self):
        maniphest = a_maniphest(task_id=7, name="commented")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["--format=json", "maniphest", "comment", "T7", "hello"]
            )

        [record] = json.loads(result.stdout)
        assert record["Task"]["Name"] == "commented"
        maniphest.add_task_comment.assert_called_once_with("T7", "hello")

    def test_a_human_format_still_prints_the_uri(self):
        maniphest = a_maniphest(task_id=7)

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["--format=rich", "maniphest", "comment", "T7", "hello"]
            )

        assert result.stdout.strip() == "https://phorge.example.com/T7"


class TestEditBatch:
    """The batch path, which `maniphest edit` and `phabfive edit` share."""

    @staticmethod
    def _maniphest(task_ids=("101",), transactions=True):
        instance = MagicMock()
        instance._get_task_data.return_value = {
            "fields": {"name": "task", "description": {"raw": ""}},
            "attachments": {"columns": {"boards": {}}},
        }
        instance.build_task_edit.return_value = (
            [{"type": "status", "value": "resolved"}] if transactions else [],
            [{"field": "Status", "old": "Open", "new": "Resolved"}]
            if transactions
            else [],
        )
        instance.task_show.return_value = {
            "tasks": [a_task_record(int(tid)) for tid in task_ids],
            "missing_ids": [],
        }
        return instance

    @staticmethod
    def _tasks(task_ids):
        return [
            {"object_type": "task", "object_id": tid, "data": {}} for tid in task_ids
        ]

    def test_emits_a_record_per_edited_task(self, capsys):
        from phabfive.cli.edit_flow import edit_tasks_batch

        maniphest = self._maniphest(("101", "102"))

        retcode = edit_tasks_batch(
            self._tasks(["101", "102"]),
            maniphest,
            status="resolved",
            force=True,
            output_format="json",
        )
        captured = capsys.readouterr()

        assert retcode == 0
        assert len(json.loads(captured.out)) == 2
        maniphest.task_show.assert_called_once_with([101, 102])

    def test_prose_moves_to_stderr(self, capsys):
        from phabfive.cli.edit_flow import edit_tasks_batch

        edit_tasks_batch(
            self._tasks(["101"]),
            self._maniphest(),
            status="resolved",
            force=True,
            output_format="json",
        )
        captured = capsys.readouterr()

        assert "Edited 1/1 tasks" in captured.err
        assert "Status: Open → Resolved" in captured.err
        # and nothing of it leaked into the stream being parsed
        json.loads(captured.out)

    def test_no_format_asked_for_prints_as_it_always_did(self, capsys):
        from phabfive.cli.edit_flow import edit_tasks_batch

        edit_tasks_batch(
            self._tasks(["101"]), self._maniphest(), status="resolved", force=True
        )
        captured = capsys.readouterr()

        assert "Edited 1/1 tasks" in captured.out
        assert captured.err == ""

    def test_a_task_needing_no_transaction_still_has_a_record(self, capsys):
        """ "Already at the target state" is an answer about the task.

        A caller parsing the stream should not have to read an empty stdout
        as "it was already right" - that is indistinguishable from a command
        that did nothing at all.
        """
        from phabfive.cli.edit_flow import edit_tasks_batch

        maniphest = self._maniphest(transactions=False)

        retcode = edit_tasks_batch(
            self._tasks(["101"]),
            maniphest,
            status="resolved",
            force=True,
            output_format="json",
        )
        captured = capsys.readouterr()

        assert retcode == 0
        assert len(json.loads(captured.out)) == 1
        assert "No changes (already at target state)" in captured.err
        maniphest.apply_task_edit.assert_not_called()

    def test_dry_run_leaves_stdout_empty(self, capsys):
        from phabfive.cli.edit_flow import edit_tasks_batch

        maniphest = self._maniphest()

        edit_tasks_batch(
            self._tasks(["101"]),
            maniphest,
            status="resolved",
            dry_run=True,
            force=True,
            output_format="json",
        )
        captured = capsys.readouterr()

        assert captured.out == ""
        assert "[DRY RUN] Would apply to T101:" in captured.err
        maniphest.task_show.assert_not_called()


class TestPasteCreate:
    """`paste create` answers with the new paste's record."""

    @pytest.mark.parametrize("output_format", MACHINE)
    def test_emits_the_show_record(self, output_format):
        paste = a_paste(paste_id=7, title="notes 344")

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app,
                [
                    f"--format={output_format}",
                    "paste",
                    "create",
                    "notes",
                    "--content=hello",
                    "--yes",
                ],
            )

        assert result.exit_code == 0
        assert "https://phorge.example.com/P7" in result.stdout
        assert "notes 344" in result.stdout
        paste.paste_show.assert_called_once_with([7])

    def test_json_stdout_parses_on_its_own(self):
        paste = a_paste(paste_id=7, title="notes 344", content="hello")

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "paste",
                    "create",
                    "notes",
                    "--content=hello",
                    "--yes",
                ],
            )

        [record] = json.loads(result.stdout)
        assert record["Link"] == "https://phorge.example.com/P7"
        assert record["Name"] == "notes 344"
        assert record["Content"] == "hello"

    @pytest.mark.parametrize("output_format", HUMAN)
    def test_a_human_format_still_prints_the_url_alone(self, output_format):
        paste = a_paste(paste_id=7)

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app,
                [
                    f"--format={output_format}",
                    "paste",
                    "create",
                    "notes",
                    "--content=hello",
                    "--yes",
                ],
            )

        assert result.stdout.strip() == "https://phorge.example.com/P7"
        paste.paste_show.assert_not_called()

    def test_dry_run_leaves_stdout_empty(self):
        paste = a_paste()

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "paste",
                    "create",
                    "notes",
                    "--content=hello",
                    "--dry-run",
                    "--yes",
                ],
            )

        assert result.stdout.strip() == ""
        assert "[DRY RUN] Would create paste:" in result.stderr
        paste.create_paste_from_content.assert_not_called()


class TestPasteEdit:
    """`paste edit` answers with the edited paste's record."""

    def test_emits_the_show_record_and_moves_prose(self):
        paste = a_paste(paste_id=7, language="python")

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app,
                ["--format=json", "paste", "edit", "P7", "--language=python", "--yes"],
            )

        [record] = json.loads(result.stdout)
        assert record["Language"] == "python"
        assert "Updated P7" in result.stderr
        paste.paste_show.assert_called_once_with([7])

    def test_a_human_format_still_prints_the_change_list(self):
        paste = a_paste(paste_id=7)

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app,
                ["--format=rich", "paste", "edit", "P7", "--language=python", "--yes"],
            )

        assert "Updated P7" in result.stdout
        assert "Language: python" in result.stdout
        paste.paste_show.assert_not_called()

    def test_an_edit_needing_nothing_still_has_a_record(self):
        """Same rule the maniphest commands settled: silence is not an answer."""
        paste = a_paste(paste_id=7)
        paste.edit_paste.return_value = {
            "paste_id": 7,
            "changes": [],
            "message": "No changes specified",
        }

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app, ["--format=json", "paste", "edit", "P7", "--yes"]
            )

        assert len(json.loads(result.stdout)) == 1
        assert "No changes specified" in result.stderr

    def test_dry_run_leaves_stdout_empty(self):
        paste = a_paste(paste_id=7)
        paste.edit_paste.return_value = {
            "paste_id": 7,
            "changes": [{"field": "Language", "new": "python"}],
            "dry_run": True,
        }

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "paste",
                    "edit",
                    "P7",
                    "--language=python",
                    "--dry-run",
                ],
            )

        assert result.stdout.strip() == ""
        assert "[DRY RUN] Would edit P7:" in result.stderr
        paste.paste_show.assert_not_called()


class TestPasteComment:
    """`paste comment` answers with the commented paste's record."""

    def test_emits_the_show_record(self):
        paste = a_paste(paste_id=7, title="notes 344")

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app, ["--format=json", "paste", "comment", "P7", "hello"]
            )

        [record] = json.loads(result.stdout)
        assert record["Name"] == "notes 344"
        paste.add_paste_comment.assert_called_once_with(7, "hello")

    def test_a_human_format_still_prints_the_url(self):
        paste = a_paste(paste_id=7)

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app, ["--format=rich", "paste", "comment", "P7", "hello"]
            )

        assert result.stdout.strip() == "https://phorge.example.com/P7"


class TestDiffusionRepoCreate:
    """`diffusion repo create` answers with the new repository's record."""

    @pytest.mark.parametrize("output_format", MACHINE)
    def test_emits_the_show_record(self, output_format):
        diffusion = a_diffusion(name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    f"--format={output_format}",
                    "diffusion",
                    "repo",
                    "create",
                    "probe",
                    "--yes",
                ],
            )

        assert result.exit_code == 0
        assert "R7" in result.stdout
        diffusion.repo_show.assert_called_once_with(["probe"])

    def test_json_stdout_parses_and_prose_moves(self):
        diffusion = a_diffusion(name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                ["--format=json", "diffusion", "repo", "create", "probe", "--yes"],
            )

        [record] = json.loads(result.stdout)
        assert record["Repository"]["Monogram"] == "R7"
        assert "Short name: probe" in result.stderr

    @pytest.mark.parametrize("output_format", HUMAN)
    def test_a_human_format_still_prints_the_change_list(self, output_format):
        diffusion = a_diffusion(name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    f"--format={output_format}",
                    "diffusion",
                    "repo",
                    "create",
                    "probe",
                    "--yes",
                ],
            )

        assert "Short name: probe" in result.stdout
        diffusion.repo_show.assert_not_called()

    def test_dry_run_leaves_stdout_empty(self):
        diffusion = a_diffusion(name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "diffusion",
                    "repo",
                    "create",
                    "probe",
                    "--dry-run",
                ],
            )

        assert result.stdout.strip() == ""
        assert "[DRY RUN] Would create probe:" in result.stderr
        diffusion.apply_repo_create.assert_not_called()


class TestDiffusionRepoEdit:
    """`diffusion repo edit` answers with the edited repository's record."""

    def test_emits_the_show_record_by_monogram(self):
        """The monogram, because --short-name can move the name underneath.

        Looking the repository up again by the identifier the caller used
        would miss it after a rename, so the edit reports by R<id>.
        """
        diffusion = a_diffusion(monogram="R7", name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "diffusion",
                    "repo",
                    "edit",
                    "probe",
                    "--default-branch=main",
                    "--yes",
                ],
            )

        [record] = json.loads(result.stdout)
        assert record["Repository"]["Monogram"] == "R7"
        diffusion.repo_show.assert_called_once_with(["R7"])
        assert "Default branch: master → main" in result.stderr

    def test_an_edit_needing_nothing_still_has_a_record(self):
        diffusion = a_diffusion(monogram="R7", name="probe")
        diffusion.build_repo_edit.return_value = ([], [])

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "diffusion",
                    "repo",
                    "edit",
                    "probe",
                    "--default-branch=main",
                    "--yes",
                ],
            )

        assert len(json.loads(result.stdout)) == 1
        assert "No changes (already at target state)" in result.stderr
        diffusion.apply_repo_edit.assert_not_called()

    def test_dry_run_leaves_stdout_empty(self):
        diffusion = a_diffusion(monogram="R7", name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "diffusion",
                    "repo",
                    "edit",
                    "probe",
                    "--default-branch=main",
                    "--dry-run",
                ],
            )

        assert result.stdout.strip() == ""
        assert "[DRY RUN] Would apply to" in result.stderr
        diffusion.apply_repo_edit.assert_not_called()


class TestDiffusionUriWrites:
    """A URI write answers with the repository's URI records.

    There is no `uri show`, and a URI has no page of its own, so the read
    command a URI write answers with is `uri list`. It answers with the
    whole set rather than the one URI named, because `uri create` demotes
    every URI already on the repository before adding the new one - the set
    is what the command actually changed, and answering `uri edit` the same
    way means a caller need not know which has the wider blast radius.
    """

    def test_create_emits_the_uri_records(self):
        diffusion = a_diffusion(name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "diffusion",
                    "uri",
                    "create",
                    "K3",
                    "probe",
                    "git@example.com:org/repo.git",
                    "--observe",
                    "--yes",
                ],
            )

        [record] = json.loads(result.stdout)
        assert record["URI"] == "git@example.com:org/repo.git"
        diffusion.uri_list.assert_called_once_with("probe")

    def test_create_under_a_human_format_still_prints_the_uri(self):
        diffusion = a_diffusion(name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=rich",
                    "diffusion",
                    "uri",
                    "create",
                    "K3",
                    "probe",
                    "git@example.com:org/repo.git",
                    "--observe",
                    "--yes",
                ],
            )

        assert result.stdout.strip() == "git@example.com:org/repo.git"
        diffusion.uri_list.assert_not_called()

    def test_edit_emits_the_whole_set_so_context_survives(self):
        diffusion = a_diffusion(
            name="probe",
            uris=[
                a_uri_record("git@example.com:org/repo.git"),
                a_uri_record("https://phorge.example.com/source/probe.git", "built-in"),
            ],
        )

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "diffusion",
                    "uri",
                    "edit",
                    "probe",
                    "git@example.com:org/repo.git",
                    "--display=never",
                    "--yes",
                ],
            )

        assert len(json.loads(result.stdout)) == 2
        assert "Display: always → never" in result.stderr

    def test_edit_dry_run_leaves_stdout_empty(self):
        diffusion = a_diffusion(name="probe")

        with patch("phabfive.cli.diffusion._get_diffusion_app", return_value=diffusion):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "diffusion",
                    "uri",
                    "edit",
                    "probe",
                    "git@example.com:org/repo.git",
                    "--display=never",
                    "--dry-run",
                ],
            )

        assert result.stdout.strip() == ""
        assert "[DRY RUN] Would apply to" in result.stderr
        diffusion.apply_uri_edit.assert_not_called()


class TestManiphestCreateFromTemplate:
    """`maniphest create --with` answers with every task it created.

    It used to answer with nothing at all on a real run:
    `create_tasks_from_yaml` returned None and its recursion kept no list of
    what it had made, so there was nothing for the CLI to report (#344).
    """

    def test_emits_a_record_per_created_task(self, tmp_path):
        maniphest = a_maniphest()
        maniphest.create_tasks_from_yaml.return_value = {"task_ids": [7, 8]}
        maniphest.task_show.return_value = {
            "tasks": [a_task_record(7, "parent"), a_task_record(8, "child")],
            "missing_ids": [],
        }
        template = tmp_path / "tasks.yaml"
        template.write_text("tasks:\n")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["--format=json", "maniphest", "create", "--with", str(template)]
            )

        records = json.loads(result.stdout)
        assert [r["Task"]["Name"] for r in records] == ["parent", "child"]
        maniphest.task_show.assert_called_once_with([7, 8])

    def test_a_human_format_still_prints_nothing(self, tmp_path):
        """Unchanged: a real template run has never printed anything."""
        maniphest = a_maniphest()
        maniphest.create_tasks_from_yaml.return_value = {"task_ids": [7]}
        template = tmp_path / "tasks.yaml"
        template.write_text("tasks:\n")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app, ["--format=rich", "maniphest", "create", "--with", str(template)]
            )

        assert result.stdout.strip() == ""
        maniphest.task_show.assert_not_called()

    def test_dry_run_preview_moves_to_stderr(self, tmp_path):
        maniphest = a_maniphest()
        maniphest.create_tasks_from_yaml.return_value = {
            "dry_run": True,
            "tasks": [{"depth": 0, "title": "parent"}],
        }
        template = tmp_path / "tasks.yaml"
        template.write_text("tasks:\n")

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=maniphest):
            result = runner.invoke(
                app,
                [
                    "--format=json",
                    "maniphest",
                    "create",
                    "--with",
                    str(template),
                    "--dry-run",
                ],
            )

        assert result.stdout.strip() == ""
        assert "- parent" in result.stderr


class TestCacheClear:
    """`cache clear` reports what it removed, as an object.

    The one write with no Phorge object behind it, so there is no `show`
    record to answer with. What it publishes instead is its own answer -
    how much went, and from where - in `cache info`'s vocabulary.
    """

    def test_reports_the_count_as_an_object(self, enabled_cache):
        from phabfive import cache as cache_module

        cache_module.set("users", "one", 1)
        cache_module.set("users", "two", 2)

        result = runner.invoke(app, ["--format=json", "cache", "clear"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "Removed": 2,
            "Namespaces": None,
            "Host": None,
            "AllInstances": False,
        }

    def test_a_namespace_is_named_without_parsing_prose(self, enabled_cache):
        from phabfive import cache as cache_module

        cache_module.set("users", "one", 1)

        result = runner.invoke(app, ["--format=json", "cache", "clear", "users"])

        assert json.loads(result.stdout)["Namespaces"] == ["users"]

    def test_jsonl_is_one_line(self, enabled_cache):
        result = runner.invoke(app, ["--format=jsonl", "cache", "clear"])

        assert len(result.stdout.strip().splitlines()) == 1
        assert json.loads(result.stdout)["Removed"] == 0

    def test_a_human_format_still_prints_the_sentence(self, enabled_cache):
        result = runner.invoke(app, ["--format=rich", "cache", "clear"])

        assert "Removed 0 cached lookups" in result.stdout


class TestStatusTextOnStderr:
    """Section 2: status and usage text is not part of the stream."""

    def test_no_pastes_found_is_on_stderr(self):
        paste = MagicMock()
        paste.get_pastes.return_value = []

        with patch("phabfive.cli.paste._get_paste_app", return_value=paste):
            result = runner.invoke(
                app, ["--format=json", "paste", "search", "zzzznope"]
            )

        assert result.exit_code == 0
        assert result.stdout.strip() == ""
        assert "No pastes found" in result.stderr

    @pytest.mark.parametrize(
        "argv, getter",
        [
            (["paste", "search"], "phabfive.cli.paste._get_paste_app"),
            (
                ["passphrase", "search"],
                "phabfive.cli.passphrase._get_passphrase_app",
            ),
            (["maniphest", "search"], "phabfive.cli.maniphest._get_maniphest_app"),
            (["user", "search"], "phabfive.user.User"),
        ],
    )
    def test_a_bare_search_prints_help_and_searches_nothing(self, argv, getter):
        """The help itself is checked for stderr out of process, below."""
        with patch(getter, return_value=MagicMock()) as get_app:
            result = runner.invoke(app, ["--format=json", *argv])

        assert result.exit_code == 2
        assert "Usage:" in result.output
        if argv[0] == "maniphest":
            # A --with template can supply the criteria, so maniphest loads
            # the app before it knows whether it has any
            get_app.return_value.task_search.assert_not_called()
        else:
            get_app.assert_not_called()

    @pytest.mark.parametrize(
        "argv",
        [["paste", "search"], ["passphrase", "search"], ["user", "search"]],
        ids=lambda argv: argv[0],
    )
    def test_the_help_of_a_bare_search_is_on_stderr(self, argv):
        """Run out of process: CliRunner imports typer before phabfive.cli can
        set TYPER_USE_RICH=0, and typer's rich path writes help to stdout.
        maniphest is left out because it needs a server before it can tell
        that it was given nothing to search for."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from phabfive.cli import cli_entrypoint; cli_entrypoint()",
                "--format=json",
                *argv,
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        assert result.stdout == ""
        assert "Usage:" in result.stderr
