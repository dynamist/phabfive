# -*- coding: utf-8 -*-

"""Tests for `paste search` paging and what --limit means.

`paste.search` was called once and the cursor in the response was never
read, so an instance with more than 100 pastes answered silently short.
--limit was forwarded to the API as the page size, so any value above 100
was answered with ERR-INVALID-PAGE-SIZE instead of more results.
"""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from phabfive.cli.paste import paste_app
from phabfive.pagination import MAX_PAGE_SIZE, search_all_pages

runner = CliRunner()

ADMIN_PHID = "PHID-USER-1234567890abcdefghij"


def _paste(id_):
    return {"id": id_, "phid": f"PHID-PSTE-{id_}", "fields": {"title": f"paste {id_}"}}


def _paged_search(pages):
    """A paste.search that answers across several pages, honouring limit.

    Conduit caps a page at 100 rows and hands back a cursor; a client that
    reads only the first response silently loses everything after it.
    """
    calls = []

    def search(**kwargs):
        calls.append(kwargs)

        if kwargs.get("limit", MAX_PAGE_SIZE) > MAX_PAGE_SIZE:
            raise AssertionError(
                "ERR-INVALID-PAGE-SIZE: Maximum page size for Conduit API "
                f"method calls is 100, but this call specified {kwargs['limit']}."
            )

        after = kwargs.get("after")
        index = 0 if after is None else int(after)
        page = pages[index][: kwargs.get("limit", MAX_PAGE_SIZE)]
        last = index + 1 >= len(pages)

        return {"data": page, "cursor": {"after": None if last else str(index + 1)}}

    search.calls = calls

    return search


class TestSearchAllPages:
    def test_follows_the_cursor(self):
        search = _paged_search([[_paste(1)], [_paste(2)]])

        assert [p["id"] for p in search_all_pages(search)] == [1, 2]

    def test_a_page_with_no_cursor_key_at_all_ends_the_walk(self):
        search = MagicMock(return_value={"data": [_paste(1)]})

        assert [p["id"] for p in search_all_pages(search)] == [1]

    def test_a_limit_spanning_pages_is_not_sent_as_the_page_size(self):
        search = _paged_search(
            [[_paste(i) for i in range(100)], [_paste(i) for i in range(100, 200)]]
        )

        pastes = search_all_pages(search, limit=150)

        assert len(pastes) == 150
        assert [call.get("limit") for call in search.calls] == [100, 50]

    def test_a_small_limit_asks_for_a_small_page(self):
        search = _paged_search([[_paste(i) for i in range(100)]])

        pastes = search_all_pages(search, limit=5)

        assert len(pastes) == 5
        assert search.calls == [{"limit": 5}]

    def test_a_limit_beyond_the_last_page_returns_what_exists(self):
        search = _paged_search([[_paste(1)], [_paste(2)]])

        assert len(search_all_pages(search, limit=250)) == 2

    def test_no_limit_leaves_the_page_size_to_the_server(self):
        search = _paged_search([[_paste(1)]])

        search_all_pages(search, queryKey="all")

        assert "limit" not in search.calls[0]


class TestGetPastesPaging:
    def _paste_app(self, search):
        from phabfive.paste import Paste

        paste = Paste.__new__(Paste)
        paste.phab = MagicMock()
        paste.phab.paste.search.side_effect = search

        return paste

    def test_every_page_is_returned(self):
        search = _paged_search([[_paste(1)], [_paste(2)]])
        paste = self._paste_app(search)

        assert [p["id"] for p in paste.get_pastes()] == [1, 2]

    def test_the_second_page_is_asked_for_with_the_cursor(self):
        search = _paged_search([[_paste(1)], [_paste(2)]])
        paste = self._paste_app(search)

        paste.get_pastes()

        assert search.calls[0].get("after") is None
        assert search.calls[1]["after"] == "1"

    def test_a_limit_above_a_page_no_longer_fails(self):
        search = _paged_search(
            [[_paste(i) for i in range(100)], [_paste(i) for i in range(100, 200)]]
        )
        paste = self._paste_app(search)

        assert len(paste.get_pastes(limit=101)) == 101


class TestPasteSearchLimitOption:
    """The CLI hands get_pastes a total, and 0 means every match."""

    def _run(self, mock_get_app, args):
        mock_p = MagicMock()
        mock_p.phab.user.whoami.return_value = {"phid": ADMIN_PHID}
        mock_p.get_pastes.return_value = [_paste(1)]
        mock_get_app.return_value = mock_p

        result = runner.invoke(paste_app, args)

        assert result.exit_code == 0

        return mock_p.get_pastes.call_args.kwargs["limit"]

    @patch("phabfive.cli.paste._get_paste_app")
    def test_the_default_is_a_hundred(self, mock_get_app):
        assert self._run(mock_get_app, ["search", "x"]) == 100

    @patch("phabfive.cli.paste._get_paste_app")
    def test_a_limit_above_a_page_is_passed_through(self, mock_get_app):
        assert self._run(mock_get_app, ["search", "x", "--limit", "250"]) == 250

    @patch("phabfive.cli.paste._get_paste_app")
    def test_zero_means_every_paste(self, mock_get_app):
        assert self._run(mock_get_app, ["search", "x", "--limit", "0"]) is None
