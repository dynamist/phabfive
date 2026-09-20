# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest
import typer
from click.shell_completion import ShellComplete

# phabfive imports
from phabfive.cli import app, completers


def _complete(args, incomplete):
    """Return what shell completion offers for the command line."""
    command = typer.main.get_command(app)
    completion = ShellComplete(command, {}, "phabfive", "_PHABFIVE_COMPLETE")
    return [item.value for item in completion.get_completions(args, incomplete)]


class TestRepoStatusCompletion:
    def test_offers_all_statuses(self):
        assert _complete(["diffusion", "repo", "list"], "") == [
            "active",
            "inactive",
            "all",
        ]

    def test_matches_prefix(self):
        assert _complete(["diffusion", "repo", "list"], "in") == ["inactive"]


class TestPolicyCompletion:
    """--view, --edit-policy and --push share one grammar and one completer."""

    @pytest.mark.parametrize("flag", ["--view", "--edit-policy", "--push"])
    def test_offers_the_keywords_and_both_prefixes(self, flag):
        """The keywords are a constant because Phorge has no policy.query to
        ask; "#" and "@" are offered as the start of a value rather than a
        whole one, so the rest of the grammar is discoverable."""
        assert _complete(["diffusion", "repo", "edit", "R5", flag], "") == [
            "public",
            "users",
            "admin",
            "no-one",
            "#",
            "@",
        ]

    def test_matches_prefix(self):
        assert _complete(["diffusion", "repo", "edit", "R5", "--view"], "no") == [
            "no-one"
        ]

    def test_a_hash_completes_projects(self):
        """A project's display name is offered rather than its hashtag, and
        works: project.search normalises the "slugs" constraint it is given,
        so "#Human Resources" resolves what "#human_resources" does."""
        with patch.object(
            completers,
            "_project_completions",
            return_value=[("infrastructure", "in Ops")],
        ) as projects:
            offered = _complete(["diffusion", "repo", "edit", "R5", "--view"], "#infra")

        assert offered == ["#infrastructure"]
        projects.assert_called_once_with("infra")

    def test_an_at_completes_usernames(self):
        with patch.object(
            completers, "_user_completions", return_value=[("admin", "Administrator")]
        ) as users:
            offered = _complete(["diffusion", "repo", "edit", "R5", "--push"], "@adm")

        assert offered == ["@admin"]
        users.assert_called_once_with("adm", include_disabled=False)

    def test_me_is_not_offered(self):
        """A policy names an account. The shortcut would have to be resolved
        against whoever is running the command, which is a different thing
        from the placeholder the search filters accept."""
        with patch.object(
            completers,
            "_user_completions",
            return_value=[("@me", "yourself"), ("admin", None)],
        ):
            offered = _complete(["diffusion", "repo", "edit", "R5", "--view"], "@")

        assert offered == ["@admin"]


class TestPassphraseTypeCompletion:
    @pytest.mark.parametrize("flag", ["--type", "-t"])
    def test_offers_all_types(self, flag):
        assert _complete(["passphrase", "search", flag], "") == [
            "password",
            "token",
            "key",
            "ssh",
            "note",
        ]

    def test_matches_prefix(self):
        assert _complete(["passphrase", "search", "--type"], "t") == ["token"]


class TestOrderCompletion:
    """--order completes progressively: fields first, directions after ":"."""

    @pytest.mark.parametrize("flag", ["--order", "-o"])
    def test_offers_the_fields(self, flag):
        assert _complete(["maniphest", "search", flag], "") == [
            "priority",
            "updated",
            "created",
            "closed",
            "title",
            "relevance",
        ]

    def test_matches_field_prefix(self):
        assert _complete(["maniphest", "search", "--order"], "c") == [
            "created",
            "closed",
        ]

    def test_offers_directions_after_the_colon(self):
        assert _complete(["maniphest", "search", "--order"], "updated:") == [
            "updated:asc",
            "updated:desc",
        ]

    def test_narrows_the_direction(self):
        assert _complete(["maniphest", "search", "--order"], "title:d") == [
            "title:desc"
        ]

    def test_directionless_field_offers_nothing(self):
        assert _complete(["maniphest", "search", "--order"], "relevance:") == []

    def test_unknown_field_offers_nothing(self):
        assert _complete(["maniphest", "search", "--order"], "bogus:") == []


class TestPasteTagCompletion:
    @pytest.mark.parametrize(
        "args", [["paste", "create", "--tag"], ["paste", "edit", "P1", "--tag"]]
    )
    def test_completes_project_names(self, args):
        phab = MagicMock()
        phab.project.search.return_value = {
            "data": [
                {"id": 1, "phid": "PHID-PROJ-1", "fields": {"name": "Backend"}},
                {"id": 2, "phid": "PHID-PROJ-2", "fields": {"name": "Frontend"}},
            ],
            "cursor": {"after": None},
        }
        with patch.object(
            completers,
            "_get_values_with_api_fallback",
            side_effect=lambda fetch, default: fetch(phab),
        ):
            assert _complete(args, "B") == ["Backend"]
