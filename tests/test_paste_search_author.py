# -*- coding: utf-8 -*-

"""Tests for the author constraint of `paste search`.

Phorge's paste.search names this constraint "authors". phabfive sent
"authorPHIDs", which is what maniphest.search calls its own author
filter, so every `paste search --author` call failed with
ERR-INVALID-CONSTRAINT.
"""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from phabfive.cli.paste import paste_app

runner = CliRunner()

ADMIN_PHID = "PHID-USER-1234567890abcdefghij"


def _mock_paste_app(mock_get_app):
    """Paste app whose whoami and user.search resolve to ADMIN_PHID.

    Except that no user is called "me", which would make @me ambiguous.
    """
    mock_p = MagicMock()
    mock_p.phab.user.whoami.return_value = {"phid": ADMIN_PHID}

    def search(constraints):
        if constraints.get("usernames") == ["me"]:
            return {"data": []}
        return {"data": [{"phid": ADMIN_PHID, "fields": {"username": "admin"}}]}

    mock_p.phab.user.search.side_effect = search
    mock_p.paste_search.return_value = {"pastes": []}
    mock_get_app.return_value = mock_p
    return mock_p


def _constraints(mock_p):
    return mock_p.paste_search.call_args.kwargs["constraints"]


class TestPasteSearchAuthorConstraint:
    @patch("phabfive.cli.paste._get_paste_app")
    def test_me_shortcut_uses_authors_constraint(self, mock_get_app):
        mock_p = _mock_paste_app(mock_get_app)

        result = runner.invoke(paste_app, ["search", "--author", "@me"])

        assert result.exit_code == 0
        constraints = _constraints(mock_p)
        assert constraints == {"authors": [ADMIN_PHID]}
        assert "authorPHIDs" not in constraints

    @patch("phabfive.cli.paste._get_paste_app")
    def test_username_uses_authors_constraint(self, mock_get_app):
        mock_p = _mock_paste_app(mock_get_app)

        result = runner.invoke(paste_app, ["search", "--author", "admin"])

        assert result.exit_code == 0
        mock_p.phab.user.search.assert_called_once_with(
            constraints={"usernames": ["admin"]}
        )
        assert _constraints(mock_p) == {"authors": [ADMIN_PHID]}

    @patch("phabfive.cli.paste._get_paste_app")
    def test_author_combines_with_text_query(self, mock_get_app):
        mock_p = _mock_paste_app(mock_get_app)

        result = runner.invoke(paste_app, ["search", "deploy", "--author", "@me"])

        assert result.exit_code == 0
        assert _constraints(mock_p) == {"query": "deploy", "authors": [ADMIN_PHID]}

    @patch("phabfive.cli.paste._get_paste_app")
    def test_unknown_user_errors_without_querying(self, mock_get_app):
        mock_p = _mock_paste_app(mock_get_app)
        mock_p.phab.user.search.side_effect = None
        mock_p.phab.user.search.return_value = {"data": []}

        result = runner.invoke(paste_app, ["search", "--author", "nosuchuser"])

        assert result.exit_code == 1
        mock_p.paste_search.assert_not_called()
