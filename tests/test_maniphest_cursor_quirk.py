# -*- coding: utf-8 -*-

"""Paging maniphest.search past a Phorge that fails on its own cursors.

Phorge before 2023.49, on PHP 8.1 or newer, fails every maniphest.search page
after the first: it calls strlen() on the missing project half of a cursor
like "123". A cursor of "123." reads the same everywhere. A server without
the bug must keep getting the requests it always got, so the workaround is
only sent after that failure, and from then on.
"""

# python std lib
from unittest.mock import MagicMock

# 3rd party imports
import pytest

# phabfive imports
from phabfive.capabilities import (
    BARE_CURSOR,
    has_quirk,
    rejects_bare_cursor,
    remember_quirk,
)
from phabfive.exceptions import PhabfiveAPIException
from phabfive.maniphest import Maniphest

STRLEN = PhabfiveAPIException(
    "ERR-CONDUIT-CORE",
    "strlen(): Passing null to parameter #1 ($string) of type string is deprecated",
)


def _page(ids, after=None):
    return {
        "data": [{"id": i, "phid": f"PHID-TASK-{i}"} for i in ids],
        "cursor": {"after": after},
    }


def _maniphest(search):
    maniphest = Maniphest.__new__(Maniphest)
    maniphest.phab = MagicMock()
    maniphest.phab.maniphest.search.side_effect = search
    return maniphest


def _buggy(**kwargs):
    """A 2023.32 on PHP 8.3: a bare cursor fails, a dotted one works."""
    after = kwargs.get("after")
    if after is None:
        return _page([3, 2], after="2")
    if after == "2":
        raise STRLEN
    assert after == "2."
    return _page([1])


def _afters(maniphest):
    return [
        call.kwargs.get("after")
        for call in maniphest.phab.maniphest.search.call_args_list
    ]


def _read(maniphest):
    return [task["id"] for task in maniphest._read_pages({})]


class TestRejectsBareCursor:
    def test_the_strlen_failure(self):
        assert rejects_bare_cursor(STRLEN)

    def test_not_another_error(self):
        assert not rejects_bare_cursor(
            PhabfiveAPIException("ERR-CONDUIT-CORE", "Something else.")
        )
        assert not rejects_bare_cursor(ValueError("strlen(): Passing null"))


def test_a_quirk_is_remembered_per_client():
    phab, other = MagicMock(), MagicMock()

    assert not has_quirk(phab, "maniphest.search", BARE_CURSOR)
    remember_quirk(phab, "maniphest.search", BARE_CURSOR)

    assert has_quirk(phab, "maniphest.search", BARE_CURSOR)
    assert not has_quirk(other, "maniphest.search", BARE_CURSOR)


class TestSearchPage:
    def test_a_server_without_the_bug_gets_the_cursor_it_gave(self):
        pages = iter([_page([3, 2], after="2"), _page([1])])
        maniphest = _maniphest(lambda **kwargs: next(pages))

        assert _read(maniphest) == [3, 2, 1]
        assert _afters(maniphest) == [None, "2"]
        assert not has_quirk(maniphest.phab, "maniphest.search", BARE_CURSOR)

    def test_the_bug_is_worked_around_and_every_page_read(self):
        maniphest = _maniphest(_buggy)

        assert _read(maniphest) == [3, 2, 1]
        assert _afters(maniphest) == [None, "2", "2."]

    def test_once_known_the_workaround_is_sent_first(self):
        maniphest = _maniphest(_buggy)
        _read(maniphest)
        maniphest.phab.maniphest.search.reset_mock()

        assert _read(maniphest) == [3, 2, 1]
        assert _afters(maniphest) == [None, "2."]

    def test_a_grouped_cursor_is_sent_as_it_is(self):
        def search(**kwargs):
            if kwargs.get("after") is None:
                return _page([2], after="2.PHID-PROJ-1")
            raise STRLEN

        maniphest = _maniphest(search)

        with pytest.raises(PhabfiveAPIException):
            _read(maniphest)
        assert _afters(maniphest) == [None, "2.PHID-PROJ-1"]

    def test_another_failure_is_not_retried(self):
        other = PhabfiveAPIException("ERR-CONDUIT-CORE", "Something else.")

        def search(**kwargs):
            if kwargs.get("after") is None:
                return _page([2], after="2")
            raise other

        maniphest = _maniphest(search)

        with pytest.raises(PhabfiveAPIException, match="Something else"):
            _read(maniphest)
        assert _afters(maniphest) == [None, "2"]
        assert not has_quirk(maniphest.phab, "maniphest.search", BARE_CURSOR)

    def test_a_workaround_that_fails_too_is_not_remembered(self):
        def search(**kwargs):
            if kwargs.get("after") is None:
                return _page([2], after="2")
            raise STRLEN

        maniphest = _maniphest(search)

        with pytest.raises(PhabfiveAPIException, match="strlen"):
            _read(maniphest)
        assert _afters(maniphest) == [None, "2", "2."]
        assert not has_quirk(maniphest.phab, "maniphest.search", BARE_CURSOR)
