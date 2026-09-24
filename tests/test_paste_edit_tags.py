# -*- coding: utf-8 -*-

"""`paste edit --tag/--untag` (#514).

`--tag` used to send whatever was typed, raw, in `projects.add`, and nothing
could remove a tag. Both now resolve each value to exactly one project and
send only what changes, the way `--subscribe/--unsubscribe` always have.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.paste import paste_app
from phabfive.exceptions import PhabfiveInputException, PhabfiveNotFoundException
from phabfive.paste import Paste

runner = CliRunner()

PROJECTS = [
    {"id": 1, "phid": "PHID-PROJ-backend", "slug": "backend", "name": "Backend"},
    {"id": 2, "phid": "PHID-PROJ-frontend", "slug": "frontend", "name": "Frontend"},
    {"id": 3, "phid": "PHID-PROJ-qa", "slug": "qa", "name": "QA"},
]


def _project_query(limit=None, offset=0):
    """project.query over PROJECTS: the name and hashtag lookup maps."""
    return {
        "data": {
            p["phid"]: {"name": p["name"], "slugs": [p["slug"]]}
            for p in PROJECTS[offset:]
        }
    }


def _project_search(constraints, attachments=None):
    """project.search over PROJECTS, by PHID or by ID."""
    phids = list(constraints.get("phids", []))
    ids = constraints.get("ids", [])

    return {
        "data": [
            {"id": p["id"], "phid": p["phid"], "fields": {"name": p["name"]}}
            for p in PROJECTS
            if p["phid"] in phids or p["id"] in ids
        ]
    }


def _paste(projects=(), subscribers=()):
    """A Paste whose P7 is tagged with ``projects``."""
    with patch("phabfive.paste.core.Phabfive.__init__", return_value=None):
        paste = Paste()
    paste.phab = MagicMock()
    paste.phab.project.query.side_effect = _project_query
    paste.phab.project.search.side_effect = _project_search
    paste.phab.paste.search.return_value = {
        "data": [
            {
                "id": 7,
                "phid": "PHID-PSTE-7",
                "fields": {"title": "Notes", "language": "text"},
                "attachments": {
                    "content": {"content": "hello"},
                    "subscribers": {"subscriberPHIDs": list(subscribers)},
                    "projects": {"projectPHIDs": list(projects)},
                },
            }
        ],
        "cursor": {"after": None},
    }
    return paste


class TestGetPasteData:
    def test_asks_for_and_exposes_the_projects(self):
        paste = _paste(["PHID-PROJ-qa"])

        data = paste.get_paste_data(7)

        assert data["projectPHIDs"] == ["PHID-PROJ-qa"]
        attachments = paste.phab.paste.search.call_args.kwargs["attachments"]
        assert attachments["projects"] is True


class TestEditPasteTags:
    def test_tag_is_resolved_to_its_phid(self):
        paste = _paste()

        result = paste.edit_paste(7, tags=["backend"])

        paste.phab.paste.edit.assert_called_once_with(
            objectIdentifier="P7",
            transactions=[{"type": "projects.add", "value": ["PHID-PROJ-backend"]}],
        )
        assert result["changes"] == [
            {"field": "Tags", "old": None, "new": "Added: Backend"}
        ]

    def test_untag_sends_projects_remove(self):
        paste = _paste(["PHID-PROJ-qa"])

        result = paste.edit_paste(7, untags=["#qa"])

        paste.phab.paste.edit.assert_called_once_with(
            objectIdentifier="P7",
            transactions=[{"type": "projects.remove", "value": ["PHID-PROJ-qa"]}],
        )
        assert [c["new"] for c in result["changes"]] == ["Removed: QA"]

    def test_only_a_change_is_sent(self):
        paste = _paste(["PHID-PROJ-backend", "PHID-PROJ-qa"])

        result = paste.edit_paste(7, tags=["Backend", "frontend"], untags=["qa"])

        paste.phab.paste.edit.assert_called_once_with(
            objectIdentifier="P7",
            transactions=[
                {"type": "projects.add", "value": ["PHID-PROJ-frontend"]},
                {"type": "projects.remove", "value": ["PHID-PROJ-qa"]},
            ],
        )
        assert [c["new"] for c in result["changes"]] == [
            "Added: Frontend",
            "Removed: QA",
        ]

    def test_one_project_named_twice_is_sent_once(self):
        paste = _paste()

        paste.edit_paste(7, tags=["backend", "#backend", "1", "PHID-PROJ-backend"])

        transactions = paste.phab.paste.edit.call_args.kwargs["transactions"]
        assert transactions == [
            {"type": "projects.add", "value": ["PHID-PROJ-backend"]}
        ]

    def test_all_at_target_reports_no_change(self):
        paste = _paste(["PHID-PROJ-backend"])

        result = paste.edit_paste(7, tags=["backend"], untags=["qa"])

        paste.phab.paste.edit.assert_not_called()
        assert result["changes"] == []
        assert result["message"] == "No changes (already at target state)"

    def test_add_and_remove_the_same_project_is_refused(self):
        paste = _paste()

        with pytest.raises(PhabfiveInputException, match="Cannot both add and remove"):
            paste.edit_paste(7, tags=["backend"], untags=["PHID-PROJ-backend"])

        paste.phab.paste.edit.assert_not_called()

    def test_unknown_project_is_named_and_nothing_sent(self):
        paste = _paste()

        with pytest.raises(PhabfiveNotFoundException, match="nosuch"):
            paste.edit_paste(7, title="New", tags=["backend", "nosuch"])

        paste.phab.paste.edit.assert_not_called()

    def test_no_wildcards(self):
        paste = _paste()

        with pytest.raises(PhabfiveInputException, match="Wildcards"):
            paste.edit_paste(7, tags=["back*"])

        paste.phab.paste.edit.assert_not_called()

    def test_dry_run_names_projects_and_sends_nothing(self):
        paste = _paste(["PHID-PROJ-qa"])

        result = paste.edit_paste(
            7, tags=["PHID-PROJ-backend"], untags=["qa"], dry_run=True
        )

        paste.phab.paste.edit.assert_not_called()
        assert result["dry_run"] is True
        assert [c["new"] for c in result["changes"]] == [
            "Added: Backend",
            "Removed: QA",
        ]

    def test_current_projects_given_are_not_fetched_again(self):
        paste = _paste()

        paste.edit_paste(7, untags=["qa"], current_projects=["PHID-PROJ-qa"])

        paste.phab.paste.search.assert_not_called()
        transactions = paste.phab.paste.edit.call_args.kwargs["transactions"]
        assert transactions == [{"type": "projects.remove", "value": ["PHID-PROJ-qa"]}]

    def test_tags_and_subscribers_share_one_fetch(self):
        paste = _paste(["PHID-PROJ-qa"], ["PHID-USER-alice"])
        paste.phab.user.search.return_value = {
            "data": [{"phid": "PHID-USER-alice", "fields": {"username": "alice"}}]
        }

        paste.edit_paste(7, untags=["qa"], unsubscribers=["PHID-USER-alice"])

        assert paste.phab.paste.search.call_count == 1


def _a_cli_paste(result=None):
    instance = MagicMock()
    instance.get_paste_data.return_value = {
        "id": 42,
        "phid": "PHID-PSTE-42",
        "title": "Notes",
        "content": "hello",
        "language": "text",
        "subscriberPHIDs": [],
        "projectPHIDs": ["PHID-PROJ-qa"],
    }
    instance.edit_paste.return_value = result or {"paste_id": 42, "changes": []}
    # Passed through as given, so what reaches edit_paste can be read back
    instance.resolve_tag_edit.side_effect = lambda tags, untags: (tags, untags)
    return instance


class TestResolveTagEdit:
    def test_resolves_both_and_refuses_one_project_in_each(self):
        paste = _paste()

        with pytest.raises(PhabfiveInputException, match="Cannot both add and remove"):
            paste.resolve_tag_edit(["backend"], ["#backend"])

    def test_what_it_returned_passes_through_edit_paste_unresolved(self):
        paste = _paste(["PHID-PROJ-qa"])
        added, removed = paste.resolve_tag_edit(["backend"], ["qa"])
        paste.phab.project.query.reset_mock()

        paste.edit_paste(7, tags=added, untags=removed)

        paste.phab.project.query.assert_not_called()
        paste.phab.paste.edit.assert_called_once_with(
            objectIdentifier="P7",
            transactions=[
                {"type": "projects.add", "value": ["PHID-PROJ-backend"]},
                {"type": "projects.remove", "value": ["PHID-PROJ-qa"]},
            ],
        )


class TestPasteEditCommand:
    def test_untag_and_the_aliases_are_merged_and_split(self):
        instance = _a_cli_paste()
        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app,
                [
                    "edit",
                    "P42",
                    "--tag=a,b",
                    "--add-tag=c",
                    "--untag=d, e",
                    "--remove-tag=f",
                ],
            )

        assert result.exit_code == 0, result.output
        kwargs = instance.edit_paste.call_args.kwargs
        assert kwargs["tags"] == ["a", "b", "c"]
        assert kwargs["untags"] == ["d", "e", "f"]
        assert kwargs["current_projects"] == ["PHID-PROJ-qa"]

    def test_aliases_are_hidden_and_untag_is_not(self):
        result = runner.invoke(paste_app, ["edit", "--help"], env={"COLUMNS": "200"})

        assert "--untag" in result.output
        assert "--add-tag" not in result.output
        assert "--remove-tag" not in result.output

    def test_dry_run_shows_the_tags_change(self):
        instance = _a_cli_paste(
            {
                "paste_id": 42,
                "dry_run": True,
                "changes": [
                    {"field": "Tags", "old": None, "new": "Added: Backend"},
                    {"field": "Tags", "old": None, "new": "Removed: QA"},
                ],
            }
        )
        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app, ["edit", "P42", "--tag=backend", "--untag=qa", "--dry-run"]
            )

        assert result.exit_code == 0, result.output
        assert "Tags: Added: Backend" in result.output
        assert "Tags: Removed: QA" in result.output

    def test_a_refused_edit_is_one_error_line(self):
        instance = _a_cli_paste()
        instance.edit_paste.side_effect = PhabfiveInputException(
            "Cannot both add and remove @bob"
        )
        with patch("phabfive.cli.paste._get_paste_app", return_value=instance):
            result = runner.invoke(
                paste_app, ["edit", "P42", "--subscribe=bob", "--unsubscribe=bob"]
            )

        assert result.exit_code == 1
        assert "Error: Cannot both add and remove @bob" in result.output

    def test_a_refused_tag_edit_is_reported_before_any_prompt(self):
        """Tags resolve before the confirmations, as maniphest edit's do."""
        instance = _a_cli_paste()
        instance.resolve_tag_edit.side_effect = PhabfiveInputException(
            "Cannot both add and remove Backend"
        )
        with (
            patch("phabfive.cli.paste._get_paste_app", return_value=instance),
            patch("phabfive.cli.editor.confirm_text_change") as confirm,
        ):
            result = runner.invoke(
                paste_app,
                [
                    "edit",
                    "P42",
                    "--content=new",
                    "--interactive",
                    "--tag=backend",
                    "--untag=backend",
                ],
            )

        assert result.exit_code == 1
        assert "Error: Cannot both add and remove Backend" in result.output
        confirm.assert_not_called()
        instance.edit_paste.assert_not_called()
