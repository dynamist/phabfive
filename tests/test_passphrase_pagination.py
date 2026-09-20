# -*- coding: utf-8 -*-

"""Tests for `passphrase search` paging and what --limit means.

`passphrase.query` was called once with --limit as the API's own limit,
while the --type and name filters run in Python afterwards - so the limit
truncated the credentials before a single one had been tested, and
`--limit 2 --type key` could report none of the keys that exist. Only the
first page was ever read, too.
"""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from phabfive.cli.passphrase import passphrase_app
from phabfive.passphrase import Passphrase

runner = CliRunner()


def _credential(id_, name, type_):
    return {
        "id": id_,
        "phid": f"PHID-CDTL-{id_}",
        "name": name,
        "type": type_,
        "username": None,
    }


def _page(credentials):
    """One passphrase.query page, keyed by PHID the way Conduit answers."""
    return {c["phid"]: c for c in credentials}


def _paged_query(pages):
    """A passphrase.query answering across several pages, with cursors."""
    calls = []

    def query(**kwargs):
        calls.append(kwargs)

        after = kwargs.get("after")
        index = 0 if after is None else int(after)
        last = index + 1 >= len(pages)

        return {
            "data": _page(pages[index]),
            "cursor": {"after": None if last else str(index + 1), "before": None},
        }

    query.calls = calls

    return query


@pytest.fixture
def passphrase():
    with patch("phabfive.passphrase.core.Phabfive.__init__", return_value=None):
        passphrase = Passphrase()
    passphrase.phab = MagicMock()
    passphrase.url = "https://phorge.example.com"
    passphrase._get_credential_dates = MagicMock(return_value=(None, None))
    return passphrase


def _names(credentials):
    return [c["name"] for c in credentials]


class TestPassphraseSearchPaging:
    def test_a_credential_on_a_later_page_is_found(self, passphrase):
        passphrase.phab.passphrase.query.side_effect = _paged_query(
            [
                [_credential(1, "Deploy password", "password")],
                [_credential(2, "Deploy key", "ssh-key-text")],
            ]
        )

        result = passphrase.search_passphrases(credential_type="key")

        assert _names(result) == ["Deploy key"]

    def test_the_second_page_is_asked_for_with_the_cursor(self, passphrase):
        query = _paged_query(
            [
                [_credential(1, "one", "password")],
                [_credential(2, "two", "password")],
            ]
        )
        passphrase.phab.passphrase.query.side_effect = query

        passphrase.search_passphrases(credential_type="password")

        assert query.calls[0].get("after") is None
        assert query.calls[1]["after"] == "1"

    def test_a_page_with_no_cursor_at_all_ends_the_walk(self, passphrase):
        passphrase.phab.passphrase.query.return_value = {
            "data": _page([_credential(1, "Deploy key", "ssh-key-text")])
        }

        assert _names(passphrase.search_passphrases(query="deploy")) == ["Deploy key"]


class TestPassphraseSearchLimit:
    """The limit counts matches, not rows the API happened to return first."""

    def _passphrase(self, passphrase, credentials):
        passphrase.phab.passphrase.query.side_effect = _paged_query([credentials])

        return passphrase

    def test_a_limit_no_longer_hides_matches_behind_non_matches(self, passphrase):
        # The bug: the first two credentials are not keys, so `--limit 2`
        # consumed the whole budget and the two keys were never tested
        self._passphrase(
            passphrase,
            [
                _credential(1, "Deploy password", "password"),
                _credential(2, "API token", "token"),
                _credential(3, "Observe key", "ssh-key-text"),
                _credential(4, "Mirror key", "ssh-generated-key"),
            ],
        )

        result = passphrase.search_passphrases(credential_type="key", limit=2)

        assert _names(result) == ["Observe key", "Mirror key"]

    def test_a_limit_truncates_the_matches(self, passphrase):
        self._passphrase(
            passphrase,
            [
                _credential(1, "key one", "ssh-key-text"),
                _credential(2, "key two", "ssh-key-text"),
                _credential(3, "key three", "ssh-key-text"),
            ],
        )

        result = passphrase.search_passphrases(credential_type="key", limit=2)

        assert _names(result) == ["key one", "key two"]

    def test_no_limit_returns_every_match(self, passphrase):
        self._passphrase(
            passphrase,
            [_credential(i, f"key {i}", "ssh-key-text") for i in range(1, 6)],
        )

        assert len(passphrase.search_passphrases(credential_type="key")) == 5

    def test_a_filled_limit_stops_before_the_next_page(self, passphrase):
        query = _paged_query(
            [
                [_credential(1, "key one", "ssh-key-text")],
                [_credential(2, "key two", "ssh-key-text")],
            ]
        )
        passphrase.phab.passphrase.query.side_effect = query

        result = passphrase.search_passphrases(credential_type="key", limit=1)

        assert _names(result) == ["key one"]
        assert len(query.calls) == 1

    def test_the_limit_is_never_sent_to_the_api(self, passphrase):
        """Sent, it would truncate before the type filter had seen anything."""
        query = _paged_query([[_credential(1, "key one", "ssh-key-text")]])
        passphrase.phab.passphrase.query.side_effect = query

        passphrase.search_passphrases(credential_type="key", limit=2)

        assert "limit" not in query.calls[0]


class TestPassphraseSearchCommandLimit:
    """The CLI hands search_passphrases a count, and 0 means every match."""

    def _run(self, mock_get_app, passphrase, args):
        passphrase.search_passphrases = MagicMock(return_value=[])
        mock_get_app.return_value = passphrase

        runner.invoke(passphrase_app, args)

        return passphrase.search_passphrases.call_args.kwargs["limit"]

    @patch("phabfive.cli.passphrase._get_passphrase_app")
    def test_the_default_is_a_hundred(self, mock_get_app, passphrase):
        assert self._run(mock_get_app, passphrase, ["search", "--type=key"]) == 100

    @patch("phabfive.cli.passphrase._get_passphrase_app")
    def test_zero_means_every_credential(self, mock_get_app, passphrase):
        args = ["search", "--type=key", "--limit", "0"]

        assert self._run(mock_get_app, passphrase, args) is None
