# -*- coding: utf-8 -*-
"""Tests for remembering the task status map between commands (#421).

Every `maniphest edit`, `create` and `search` needs maniphest.querystatuses,
and the answer only changes when somebody reconfigures Phorge. The command
keeps it in the cache; a program, which has no lookup store, asks every time.
A failed lookup is answered with the standard statuses, which must never be
written down as though the server had said them.
"""

# python std lib
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest

# phabfive imports
from phabfive import cache
from phabfive.cli.apps import new_app
from phabfive.cli.lookups import CommandLookups
from phabfive.constants import CACHE_TTLS, STATUS_MAP_CACHE_NAMESPACE
from phabfive.edit import Edit
from phabfive.exceptions import PhabfiveConfigException
from phabfive.maniphest import Maniphest
from tests.conftest import CONF

STATUSES = {
    "defaultStatus": "open",
    "openStatuses": ["open"],
    "closedStatuses": {"1": "resolved"},
    "allStatuses": ["open", "resolved"],
    "statusMap": {"open": "Open", "resolved": "Resolved"},
}

WITH_BLOCKED = {
    **STATUSES,
    "openStatuses": ["open", "blocked"],
    "allStatuses": ["open", "blocked", "resolved"],
    "statusMap": {"open": "Open", "blocked": "Blocked", "resolved": "Resolved"},
}


def _phab(answer=STATUSES, broken=False):
    phab = MagicMock()
    if broken:
        phab.maniphest.querystatuses.side_effect = RuntimeError("no such method")
    else:
        phab.maniphest.querystatuses.return_value = answer
    return phab


def _maniphest(phab, store=True):
    """A Maniphest on a fake client, with the store the command gives it."""
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        app = Maniphest()

    app.phab = phab
    app.conf = dict(CONF)
    if store:
        app.lookup_store = CommandLookups(app.conf)
    return app


def _remembered():
    directory, ttl = cache.context(STATUS_MAP_CACHE_NAMESPACE, conf=dict(CONF))
    return cache.get(STATUS_MAP_CACHE_NAMESPACE, "all", ttl=ttl, directory=directory)


class TestRemembered:
    def test_the_next_command_asks_the_server_nothing(self, enabled_cache):
        first = _phab()
        assert _maniphest(first)._get_api_status_map() == STATUSES

        second = _phab()
        assert _maniphest(second)._get_api_status_map() == STATUSES

        first.maniphest.querystatuses.assert_called_once()
        second.maniphest.querystatuses.assert_not_called()

    def test_the_whole_answer_is_kept(self, enabled_cache):
        _maniphest(_phab())._get_api_status_map()

        assert _remembered() == STATUSES

    def test_the_clients_result_wrapper_is_kept_too(self, enabled_cache):
        """The real client answers with a phabricator.Result, not a dict,
        and json cannot serialise one."""
        from phabricator import Result

        _maniphest(_phab(Result(dict(STATUSES))))._get_api_status_map()

        assert _remembered() == STATUSES

    def test_every_command_asks_when_caching_is_off(self):
        phab = _phab()
        for _ in range(2):
            _maniphest(phab)._get_api_status_map()

        assert phab.maniphest.querystatuses.call_count == 2
        assert _remembered() is cache.MISS

    def test_a_program_without_a_store_writes_nothing(self, enabled_cache):
        phab = _phab()
        for _ in range(2):
            _maniphest(phab, store=False)._get_api_status_map()

        assert phab.maniphest.querystatuses.call_count == 2
        assert _remembered() is cache.MISS

    def test_the_namespace_is_fresh_for_a_week(self):
        assert CACHE_TTLS[STATUS_MAP_CACHE_NAMESPACE] == 7 * 24 * 3600

    def test_the_namespace_can_be_cleared_by_name(self):
        assert STATUS_MAP_CACHE_NAMESPACE in cache.known_namespaces()


class TestNonAnswers:
    def test_a_broken_endpoint_falls_back_without_remembering(self, enabled_cache):
        status_map = _maniphest(_phab(broken=True))._get_api_status_map()

        assert "resolved" in status_map["statusMap"]
        assert _remembered() is cache.MISS

    def test_a_broken_endpoint_is_asked_again_next_time(self, enabled_cache):
        _maniphest(_phab(broken=True))._get_api_status_map()

        phab = _phab()
        assert _maniphest(phab)._get_api_status_map() == STATUSES
        phab.maniphest.querystatuses.assert_called_once()

    def test_an_answer_without_statuses_is_not_remembered(self, enabled_cache):
        _maniphest(_phab(answer={"statusMap": {}}))._get_api_status_map()

        assert _remembered() is cache.MISS

    def test_a_corrupt_entry_is_a_miss(self, enabled_cache):
        CommandLookups(dict(CONF)).set(STATUS_MAP_CACHE_NAMESPACE, ["open"])

        phab = _phab()
        assert _maniphest(phab)._get_api_status_map() == STATUSES
        phab.maniphest.querystatuses.assert_called_once()


class TestNewStatus:
    """A status configured after the map was remembered can still be set."""

    def test_an_unknown_status_asks_the_server_again(self, enabled_cache):
        _maniphest(_phab(STATUSES))._get_api_status_map()

        phab = _phab(WITH_BLOCKED)
        app = _maniphest(phab)

        assert app._validate_status("Blocked") == "blocked"
        phab.maniphest.querystatuses.assert_called_once()
        assert _remembered() == WITH_BLOCKED
        assert app._get_api_status_map() == WITH_BLOCKED

    def test_a_status_the_server_does_not_know_either_is_refused(self, enabled_cache):
        _maniphest(_phab(STATUSES))._get_api_status_map()

        phab = _phab(STATUSES)
        app = _maniphest(phab)

        for _ in range(2):
            with pytest.raises(PhabfiveConfigException, match="Invalid status"):
                app._validate_status("bogus")

        # Asked once to be sure, not once per refusal
        phab.maniphest.querystatuses.assert_called_once()

    def test_a_failed_refetch_keeps_the_remembered_map(self, enabled_cache):
        """A broken endpoint must not swap the server's own statuses for the
        standard ones, or a status this server lacks would be accepted."""
        custom = {
            "defaultStatus": "open",
            "openStatuses": ["open", "blocked"],
            "closedStatuses": {"1": "done"},
            "allStatuses": ["open", "blocked", "done"],
            "statusMap": {"open": "Open", "blocked": "Blocked", "done": "Done"},
        }
        _maniphest(_phab(custom))._get_api_status_map()

        phab = _phab(broken=True)
        app = _maniphest(phab)

        with pytest.raises(PhabfiveConfigException, match="Invalid status"):
            app._validate_status("wontfix")

        phab.maniphest.querystatuses.assert_called_once()
        assert app._get_api_status_map() == custom
        assert app._get_closed_statuses() == ["done"]
        assert _remembered() == custom

    def test_a_freshly_fetched_map_is_not_asked_twice(self, enabled_cache):
        phab = _phab(STATUSES)

        with pytest.raises(PhabfiveConfigException):
            _maniphest(phab)._validate_status("bogus")

        phab.maniphest.querystatuses.assert_called_once()


class TestTheCommandOptsIn:
    def test_new_app_gives_the_app_a_store(self):
        app = MagicMock()
        app.conf = dict(CONF)
        cls = MagicMock(return_value=app)

        assert isinstance(new_app(cls).lookup_store, CommandLookups)

    def test_a_program_has_none(self):
        assert Maniphest.lookup_store is None

    def test_edit_passes_it_to_the_maniphest_it_edits_through(self):
        """`maniphest edit` goes through Edit, the batch case #421 is about."""
        with patch("phabfive.edit.core.Phabfive.__init__", return_value=None):
            edit = Edit()
        edit.conf = dict(CONF)
        edit.phab = MagicMock()
        edit.lookup_store = CommandLookups(edit.conf)

        assert edit.maniphest.lookup_store is edit.lookup_store

    def test_entries_are_filed_under_the_apps_instance(self, enabled_cache):
        """Not the instance reading the configuration again would name."""
        other = {**CONF, "PHAB_URL": "https://other.example.com/api/"}
        CommandLookups(other).set(STATUS_MAP_CACHE_NAMESPACE, STATUSES)

        assert CommandLookups(other).get(STATUS_MAP_CACHE_NAMESPACE) == STATUSES
        assert CommandLookups(dict(CONF)).get(STATUS_MAP_CACHE_NAMESPACE) is None
