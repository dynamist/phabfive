# -*- coding: utf-8 -*-

"""Commits on a task: ``--commit`` on create and edit, ``commits:`` in a
creation template, and the Commits section of ``maniphest show``.

A commit is named the ways Diffusion accepts - rCALLSIGN<hash>, R1:<hash>, a
bare hash of seven or more characters, or a PHID - and each is resolved before
anything is written, the way subscribers are.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.text import Text
from ruamel.yaml import YAML
from typer.testing import CliRunner

from phabfive.cli import app
from phabfive.commits import fetch_commit_handles, resolve_commit_phids
from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveDataException,
    PhabfiveInputException,
    PhabfiveNotFoundException,
)
from phabfive.maniphest.core import Maniphest

runner = CliRunner()

HASH = "7d7fc2c3e0023069cb381fa88cc08b559d40afb0"

# name, PHID, full hash, summary, the identifiers that find it
COMMITS = [
    (
        "rGUNNAR7d7fc2c3e002",
        "PHID-CMIT-gunnar",
        HASH,
        "Sketch the telemetry frame format",
        {"rGUNNAR7d7fc2c3e002", "R1:7d7fc2c3e002", "7d7fc2c", HASH},
    ),
    (
        "R10:3ce278cd9912",
        "PHID-CMIT-r10",
        "3ce278cd99121799a648bc0366256cba18d109c4",
        "refactor: name the policy keyword arguments",
        {"R10:3ce278cd9912", "3ce278cd9912"},
    ),
    # The same commit in a fork: a bare hash finds both
    (
        "rFORK0abcdef1234",
        "PHID-CMIT-fork1",
        "0abcdef1234",
        "In the original",
        {"rFORK0abcdef1234", "0abcdef"},
    ),
    (
        "rMAIN0abcdef1234",
        "PHID-CMIT-fork2",
        "0abcdef1234",
        "In the fork",
        {"rMAIN0abcdef1234", "0abcdef"},
    ),
]


def _phab(attached=()):
    """A client that knows the commits above, with `attached` on the task."""
    phab = MagicMock()

    def commit_search(constraints):
        [identifier] = constraints["identifiers"]
        return {
            "data": [
                {"phid": phid, "fields": {"identifier": full}}
                for _, phid, full, _, found_by in COMMITS
                if identifier in found_by
            ]
        }

    def phid_query(phids):
        handles = {
            phid: {
                "phid": phid,
                "type": "CMIT",
                "name": name,
                "fullName": f"{name}: {summary}",
                "uri": f"http://phorge.localhost/{name}",
            }
            for name, phid, _, summary, _ in COMMITS
        }
        handles["PHID-TASK-1"] = {"phid": "PHID-TASK-1", "type": "TASK", "name": "T1"}
        return {phid: handles[phid] for phid in phids if phid in handles}

    phab.diffusion.commit.search.side_effect = commit_search
    phab.phid.query.side_effect = phid_query
    phab.edge.search.return_value = {
        "data": [{"destinationPHID": phid} for phid in attached]
    }
    phab.project.search.return_value = {"data": []}
    phab.maniphest.edit.return_value = {"object": {"id": 1, "phid": "PHID-TASK-1"}}
    return phab


def _maniphest(phab):
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        maniphest = Maniphest()
    maniphest.phab = phab
    maniphest.url = "http://phorge.localhost"
    maniphest.conf = {}
    return maniphest


def _task():
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
            "columns": {"boards": {}},
            "projects": {"projectPHIDs": []},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


class TestResolveCommitPhids:
    @pytest.mark.parametrize(
        "value", ["rGUNNAR7d7fc2c3e002", "R1:7d7fc2c3e002", "7d7fc2c", HASH]
    )
    def test_every_spelling_names_the_same_commit(self, value):
        assert resolve_commit_phids(_phab(), [value]) == {
            value: ("PHID-CMIT-gunnar", "rGUNNAR7d7fc2c3e002")
        }

    def test_a_phid_is_asked_about_not_passed_through(self):
        phab = _phab()

        assert resolve_commit_phids(phab, ["PHID-CMIT-r10"]) == {
            "PHID-CMIT-r10": ("PHID-CMIT-r10", "R10:3ce278cd9912")
        }
        phab.diffusion.commit.search.assert_not_called()

    def test_a_phid_of_something_else_is_not_a_commit(self):
        with pytest.raises(PhabfiveNotFoundException, match="'PHID-TASK-1'"):
            resolve_commit_phids(_phab(), ["PHID-TASK-1"])

    def test_every_unknown_one_is_named(self):
        with pytest.raises(PhabfiveNotFoundException) as excinfo:
            resolve_commit_phids(
                _phab(), ["deadbeefdead", "7d7fc2c", "rNOPE1234567"], option="--attach"
            )

        assert str(excinfo.value) == (
            "No such commit for --attach: 'deadbeefdead', 'rNOPE1234567'"
        )

    def test_a_short_hash_says_why(self):
        with pytest.raises(PhabfiveNotFoundException, match="at least 7 characters"):
            resolve_commit_phids(_phab(), ["7d7f"])

    def test_a_hash_in_two_repositories_is_ambiguous(self):
        with pytest.raises(PhabfiveInputException) as excinfo:
            resolve_commit_phids(_phab(), ["0abcdef"], option="--attach")

        message = str(excinfo.value)
        assert "'0abcdef' matches rFORK0abcdef1234, rMAIN0abcdef1234" in message
        assert "Name the repository" in message

    def test_the_order_given_is_kept(self):
        resolved = resolve_commit_phids(_phab(), ["R10:3ce278cd9912", "7d7fc2c"])

        assert list(resolved) == ["R10:3ce278cd9912", "7d7fc2c"]

    def test_one_value_is_searched_once(self):
        phab = _phab()

        resolve_commit_phids(phab, ["7d7fc2c", "7d7fc2c"])

        assert phab.diffusion.commit.search.call_count == 1


class TestFetchCommitHandles:
    def test_a_commit_is_described_by_monogram_and_summary(self):
        assert fetch_commit_handles(_phab(), ["PHID-CMIT-r10"]) == [
            {
                "Link": "http://phorge.localhost/R10:3ce278cd9912",
                "Commit": {
                    "Identifier": "R10:3ce278cd9912",
                    "Summary": "refactor: name the policy keyword arguments",
                },
            }
        ]

    def test_one_the_viewer_cannot_see_is_left_out(self):
        handles = fetch_commit_handles(_phab(), ["PHID-CMIT-hidden", "PHID-CMIT-r10"])

        assert [h["Commit"]["Identifier"] for h in handles] == ["R10:3ce278cd9912"]


class TestEdit:
    def test_a_commit_is_attached(self):
        maniphest = _maniphest(_phab())

        transactions, changes = maniphest.build_task_edit(
            "42", _task(), attach=["7d7fc2c,R10:3ce278cd9912"]
        )

        assert transactions == [
            {"type": "commits.add", "value": ["PHID-CMIT-gunnar", "PHID-CMIT-r10"]}
        ]
        assert changes == [
            {
                "field": "Commits",
                "old": None,
                "new": "Added: rGUNNAR7d7fc2c3e002, R10:3ce278cd9912",
            }
        ]

    def test_one_already_attached_is_skipped(self):
        maniphest = _maniphest(_phab(attached=["PHID-CMIT-gunnar"]))

        transactions, _ = maniphest.build_task_edit(
            "42", _task(), attach=["rGUNNAR7d7fc2c3e002", "R10:3ce278cd9912"]
        )

        assert transactions == [{"type": "commits.add", "value": ["PHID-CMIT-r10"]}]

    def test_one_commit_spelled_twice_is_attached_once(self):
        maniphest = _maniphest(_phab())

        transactions, _ = maniphest.build_task_edit(
            "42", _task(), attach=["7d7fc2c", "rGUNNAR7d7fc2c3e002"]
        )

        assert transactions == [{"type": "commits.add", "value": ["PHID-CMIT-gunnar"]}]

    def test_all_attached_already_is_no_change(self):
        maniphest = _maniphest(_phab(attached=["PHID-CMIT-gunnar"]))

        assert maniphest.build_task_edit("42", _task(), attach=["7d7fc2c"]) == ([], [])

    def test_a_commit_is_detached(self):
        maniphest = _maniphest(_phab(attached=["PHID-CMIT-gunnar", "PHID-CMIT-r10"]))

        transactions, changes = maniphest.build_task_edit(
            "42", _task(), detach=["7d7fc2c"]
        )

        assert transactions == [
            {"type": "commits.remove", "value": ["PHID-CMIT-gunnar"]}
        ]
        assert changes == [
            {"field": "Commits", "old": None, "new": "Removed: rGUNNAR7d7fc2c3e002"}
        ]

    def test_one_not_attached_is_not_detached(self):
        maniphest = _maniphest(_phab(attached=["PHID-CMIT-r10"]))

        assert maniphest.build_task_edit("42", _task(), detach=["7d7fc2c"]) == ([], [])

    def test_attach_and_detach_in_one_edit(self):
        maniphest = _maniphest(_phab(attached=["PHID-CMIT-r10"]))

        transactions, _ = maniphest.build_task_edit(
            "42", _task(), attach=["7d7fc2c"], detach=["R10:3ce278cd9912"]
        )

        assert transactions == [
            {"type": "commits.add", "value": ["PHID-CMIT-gunnar"]},
            {"type": "commits.remove", "value": ["PHID-CMIT-r10"]},
        ]

    def test_one_commit_both_attached_and_detached_is_refused(self):
        """However each was spelled."""
        maniphest = _maniphest(_phab())

        with pytest.raises(PhabfiveInputException, match="both add and remove"):
            maniphest.build_task_edit(
                "42", _task(), attach=["7d7fc2c"], detach=["rGUNNAR7d7fc2c3e002"]
            )

    def test_an_unknown_commit_names_the_option(self):
        maniphest = _maniphest(_phab())

        with pytest.raises(PhabfiveNotFoundException, match="for --detach"):
            maniphest.build_task_edit("42", _task(), detach=["deadbeefdead"])

    def test_a_failed_lookup_of_what_is_attached_is_not_no_change(self):
        """`show` answers a failed edge lookup with nothing; an edit must not,
        or detaching would report no change and send nothing."""
        phab = _phab()
        phab.edge.search.side_effect = PhabfiveAPIException("ERR-X", "boom")

        with pytest.raises(PhabfiveAPIException):
            _maniphest(phab).build_task_edit("42", _task(), detach=["7d7fc2c"])

    def test_without_commits_the_edges_are_not_asked_for(self):
        phab = _phab()

        _maniphest(phab).build_task_edit("42", _task(), title="Renamed")

        phab.edge.search.assert_not_called()


class TestCreate:
    def test_commits_are_set(self):
        phab = _phab()

        _maniphest(phab).create_task("A task", commits=["7d7fc2c", "R10:3ce278cd9912"])

        transactions = phab.maniphest.edit.call_args_list[0].kwargs["transactions"]
        assert {
            "type": "commits.set",
            "value": ["PHID-CMIT-gunnar", "PHID-CMIT-r10"],
        } in transactions

    def test_a_dry_run_names_them(self):
        result = _maniphest(_phab()).create_task(
            "A task", commits=["7d7fc2c,rGUNNAR7d7fc2c3e002"], dry_run=True
        )

        assert result["commits"] == ["rGUNNAR7d7fc2c3e002"]

    def test_an_unknown_commit_creates_nothing(self):
        phab = _phab()

        with pytest.raises(PhabfiveNotFoundException):
            _maniphest(phab).create_task("A task", commits=["deadbeefdead"])

        phab.maniphest.edit.assert_not_called()


class TestTemplate:
    def _create(self, phab, commits, variables=None):
        return _maniphest(phab).create_tasks_from_config(
            {
                "variables": variables or {},
                "tasks": [{"title": "Task", "description": "x", "commits": commits}],
            }
        )

    def test_commits_are_added(self):
        """A create spec never sends a `.set`, commits included."""
        phab = _phab()

        self._create(phab, ["7d7fc2c", "PHID-CMIT-r10"])

        transactions = phab.maniphest.edit.call_args_list[0].kwargs["transactions"]
        assert {
            "type": "commits.add",
            "value": ["PHID-CMIT-gunnar", "PHID-CMIT-r10"],
        } in transactions

    def test_they_render_variables(self):
        phab = _phab()

        self._create(phab, ["{{ sha }}"], variables={"sha": "7d7fc2c"})

        transactions = phab.maniphest.edit.call_args_list[0].kwargs["transactions"]
        assert {"type": "commits.add", "value": ["PHID-CMIT-gunnar"]} in transactions

    def test_an_unknown_commit_creates_nothing(self):
        phab = _phab()

        with pytest.raises(
            PhabfiveDataException, match="No such commit: 'deadbeefdead'"
        ):
            self._create(phab, ["deadbeefdead"])

        phab.maniphest.edit.assert_not_called()


class TestCli:
    def test_edit_passes_commit_through(self):
        with (
            patch("phabfive.cli.maniphest._get_edit_app"),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(
                app, ["maniphest", "edit", "T1", "--attach", "7d7fc2c", "--yes"]
            )

        assert result.exit_code == 0, result.output
        assert run_edit.call_args.kwargs["attach"] == ["7d7fc2c"]

    def test_phabfive_edit_passes_commit_through(self):
        with (
            patch("phabfive.cli.edit._get_edit_app", create=True),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(app, ["edit", "T1", "--attach", "7d7fc2c", "--yes"])

        assert result.exit_code == 0, result.output
        assert run_edit.call_args.kwargs["attach"] == ["7d7fc2c"]

    @pytest.mark.parametrize(
        "args",
        [
            ["maniphest", "edit", "T1"],
            ["edit", "T1"],
        ],
        ids=["maniphest-edit", "phabfive-edit"],
    )
    def test_the_hidden_aliases_join_the_options(self, args):
        with (
            patch("phabfive.cli.maniphest._get_edit_app"),
            patch("phabfive.cli.edit._get_edit_app", create=True),
            patch("phabfive.cli.edit_flow.run_edit", return_value=0) as run_edit,
        ):
            result = runner.invoke(
                app,
                [
                    *args,
                    "--attach=7d7fc2c",
                    "--add-commit=R10:3ce278cd9912",
                    "--detach=0abcdef",
                    "--remove-commit=rFORK0abcdef1234",
                    "--yes",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = run_edit.call_args.kwargs
        assert kwargs["attach"] == ["7d7fc2c", "R10:3ce278cd9912"]
        assert kwargs["detach"] == ["0abcdef", "rFORK0abcdef1234"]

    def test_help_offers_attach_and_detach_only(self):
        result = runner.invoke(app, ["maniphest", "edit", "--help"])

        assert "--attach" in result.output
        assert "--detach" in result.output
        assert "--add-commit" not in result.output
        assert "--remove-commit" not in result.output

    def test_create_names_them_in_a_dry_run(self):
        instance = MagicMock()
        instance.create_task.return_value = {
            "dry_run": True,
            "title": "A task",
            "commits": ["rGUNNAR7d7fc2c3e002"],
        }

        with patch("phabfive.cli.maniphest._get_maniphest_app", return_value=instance):
            result = runner.invoke(
                app,
                [
                    "maniphest",
                    "create",
                    "A task",
                    "--description=x",
                    "--attach=7d7fc2c",
                    "--dry-run",
                ],
            )

        assert result.exit_code == 0, result.output
        assert instance.create_task.call_args.kwargs["commits"] == ["7d7fc2c"]
        assert "Commits: rGUNNAR7d7fc2c3e002" in result.output


COMMIT = {
    "Link": "http://phorge.localhost/rGUNNAR7d7fc2c3e002",
    "Commit": {
        "Identifier": "rGUNNAR7d7fc2c3e002",
        "Summary": "Sketch the telemetry frame format",
    },
}


def _task_dict(commits):
    return {
        "_link": "http://phorge.localhost/T42",
        "_url": "http://phorge.localhost/T42",
        "Task": {"Name": "A task"},
        "Parents": [],
        "Subtasks": [],
        "Commits": commits,
    }


def _linking_instance():
    """Links as a terminal that supports them gets them."""
    instance = MagicMock()
    instance.url = "http://phorge.localhost"

    def format_link(url, text, show_url=True):
        linked = Text(text)
        linked.stylize(f"link {url}")
        return linked

    instance.format_link.side_effect = format_link
    return instance


class TestShow:
    def test_json_always_carries_the_key(self):
        from phabfive.display import _build_task_json_output

        assert _build_task_json_output(_task_dict([]))["Commits"] == []
        assert _build_task_json_output(_task_dict([COMMIT]))["Commits"] == [COMMIT]

    def test_yaml(self, capsys):
        from phabfive.display import _display_task_yaml

        _display_task_yaml(_task_dict([COMMIT]))

        parsed = YAML(typ="safe").load(StringIO(capsys.readouterr().out))
        assert parsed[0]["Commits"] == [COMMIT]

    def test_rich_links_the_monogram(self):
        from phabfive.display import _display_task_rich

        printed = []
        console = MagicMock()
        console.print.side_effect = lambda line="", **kw: printed.append(line)

        _display_task_rich(console, _task_dict([COMMIT]), _linking_instance())

        [line] = [p for p in printed if isinstance(p, Text) and "rGUNNAR" in p.plain]
        assert line.plain == (
            "    - rGUNNAR7d7fc2c3e002: Sketch the telemetry frame format"
        )
        assert any(str(span.style) == f"link {COMMIT['Link']}" for span in line.spans)

    def test_rich_leaves_out_an_empty_section(self):
        from phabfive.display import _display_task_rich

        printed = []
        console = MagicMock()
        console.print.side_effect = lambda line="", **kw: printed.append(str(line))

        _display_task_rich(console, _task_dict([]), _linking_instance())

        assert "  Commits:" not in printed

    def test_tree(self):
        from phabfive.display import _display_task_tree

        console = MagicMock()

        _display_task_tree(console, _task_dict([COMMIT]), _linking_instance())

        [tree] = console.print.call_args.args
        [branch] = [c for c in tree.children if str(c.label) == "Commits"]
        [leaf] = branch.children
        assert leaf.label.plain == (
            "rGUNNAR7d7fc2c3e002: Sketch the telemetry frame format"
        )


class TestSpecResolver:
    def _resolve(self, values):
        from phabfive.spec.online import CommitResolver

        instance = MagicMock()
        instance.phab = _phab()
        return CommitResolver().resolve(instance, values)

    def test_every_spelling_resolves_to_the_monogram(self):
        results = self._resolve(["7d7fc2c", "PHID-CMIT-r10"])

        assert results["7d7fc2c"].phid == "PHID-CMIT-gunnar"
        assert results["7d7fc2c"].label == "rGUNNAR7d7fc2c3e002"
        assert results["PHID-CMIT-r10"].label == "R10:3ce278cd9912"

    def test_each_failure_is_reported_on_its_own(self):
        results = self._resolve(["deadbeefdead", "0abcdef", "7d7f"])

        assert results["deadbeefdead"].problem == "unknown-commit"
        assert results["0abcdef"].problem == "ambiguous-commit"
        assert "rFORK0abcdef1234, rMAIN0abcdef1234" in results["0abcdef"].reason
        assert "at least 7 characters" in results["7d7f"].reason
