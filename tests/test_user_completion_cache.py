# -*- coding: utf-8 -*-
"""Tests for caching of username completion.

The fixtures mirror tests/test_user_completion.py; what these add is the
question that file cannot ask: how many times the API was actually called.
"""

# python std lib
import json
import os
import time
from unittest.mock import patch

# 3rd party imports
import pytest

# phabfive imports
from phabfive import cache
from phabfive.cli import completers
from phabfive.cli.completers import (
    USER_COMPLETION_LIMIT,
    complete_user_list_filter,
    complete_user,
    complete_user_filter,
)
from tests.test_user_completion import USERS, _phab, _user

CONF = {
    "PHAB_URL": "https://phorge.example.com/api/",
    "PHAB_TOKEN": "api-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "PHAB_CACHE": True,
    "PHAB_CACHE_TTL": 0,
    "PHAB_CACHE_DIR": "",
}


@pytest.fixture
def enabled_cache(monkeypatch):
    """Switch the cache on, with configuration that needs no real files."""
    monkeypatch.setenv("PHAB_CACHE", "1")
    with patch("phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)):
        yield


class Api:
    """A fake API client that remembers how often it was asked."""

    def __init__(self, users, page_size=100):
        self.phab = _phab(users, page_size=page_size)

    @property
    def calls(self):
        return self.phab.user.search.call_count

    def complete(self, incomplete, completer=complete_user):
        with patch.object(
            completers,
            "_get_values_with_api_fallback",
            side_effect=lambda fetch, default: fetch(self.phab),
        ):
            return completer(incomplete)

    def unavailable(self, incomplete, completer=complete_user):
        """Complete while the API cannot be reached at all."""
        with patch.object(
            completers,
            "_get_values_with_api_fallback",
            side_effect=lambda fetch, default: default,
        ):
            return completer(incomplete)


def _values(completions):
    return [c[0] if isinstance(c, tuple) else c for c in completions]


class TestRepeatedCompletion:
    def test_second_tab_on_the_same_word_asks_the_api_nothing(self, enabled_cache):
        """The double TAB that bash makes normal costs one lookup, not two."""
        api = Api(USERS)

        first = api.complete("son")
        after_first = api.calls
        second = api.complete("son")

        assert after_first > 0
        assert api.calls == after_first
        assert second == first
        assert _values(second) == ["sonja.bergstrom"]

    def test_the_lookup_is_actually_written_to_disk(self, enabled_cache):
        Api(USERS).complete("son")

        entry = cache.get("users", "enabled|son")
        assert entry is not cache.MISS
        # What the server returned, not what was offered: nameLike matched
        # "Tommy Svensson" on his real name. Keeping the superset is what lets
        # a longer prefix be narrowed out of it later.
        assert [r["username"] for r in entry["records"]] == [
            "sonja.bergstrom",
            "tommy.svensson",
        ]

    def test_a_cold_cache_still_asks_the_api(self, enabled_cache):
        api = Api(USERS)
        api.complete("son")
        assert api.calls > 0


class TestNarrowing:
    def test_a_longer_prefix_is_served_from_a_shorter_lookup(self, enabled_cache):
        """nameLike is a substring match, so "so" already contains "son"."""
        api = Api(USERS)

        api.complete("so", completer=complete_user_filter)
        after_first = api.calls
        result = api.complete("son", completer=complete_user_filter)

        assert api.calls == after_first
        assert _values(result) == ["sonja.bergstrom"]

    def test_one_empty_lookup_serves_every_prefix(self, enabled_cache):
        api = Api(USERS)

        api.complete("", completer=complete_user_filter)
        after_first = api.calls

        assert _values(api.complete("s", completer=complete_user_filter)) == [
            "sofia.lind",
            "sonja.bergstrom",
            "sven.retired",
        ]
        assert _values(api.complete("so", completer=complete_user_filter)) == [
            "sofia.lind",
            "sonja.bergstrom",
        ]
        assert _values(api.complete("son", completer=complete_user_filter)) == [
            "sonja.bergstrom"
        ]
        assert api.calls == after_first

    @pytest.mark.parametrize("text", ["", "s", "so", "son", "to", "bot", "zzz", "B"])
    def test_narrowed_answers_match_the_uncached_ones(self, enabled_cache, text):
        """Narrowing is an optimisation; it must not change a single answer."""
        warm = Api(USERS)
        warm.complete("", completer=complete_user_filter)

        with patch.object(cache, "enabled", return_value=False):
            uncached = Api(USERS).complete(text, completer=complete_user_filter)

        assert warm.complete(text, completer=complete_user_filter) == uncached

    def test_a_narrowed_lookup_is_not_written_back(self, enabled_cache):
        """Derived answers cost nothing to recompute, so they earn no file."""
        api = Api(USERS)
        api.complete("", completer=complete_user_filter)
        api.complete("son", completer=complete_user_filter)

        namespace_dir = os.path.join(cache.instance_dir(), "users")
        assert len(os.listdir(namespace_dir)) == 1

    def test_a_shorter_prefix_of_another_scope_is_not_used(self, enabled_cache):
        api = Api(USERS)
        api.complete("", completer=complete_user_filter)
        after_first = api.calls

        api.complete("son", completer=complete_user)

        assert api.calls > after_first


class TestTruncation:
    """A lookup that stopped at the limit is an arbitrary subset, so nothing
    can be narrowed out of it - but it is still its own answer."""

    def _many_users(self):
        return [_user(i, f"user{i:03d}") for i in range(USER_COMPLETION_LIMIT + 100)]

    def test_a_truncated_lookup_is_recorded_as_such(self, enabled_cache):
        Api(self._many_users()).complete("user", completer=complete_user_filter)
        assert cache.get("users", "all|user")["truncated"] is True

    def test_a_truncated_lookup_cannot_be_narrowed(self, enabled_cache):
        api = Api(self._many_users())

        api.complete("user", completer=complete_user_filter)
        after_first = api.calls
        api.complete("user1", completer=complete_user_filter)

        assert api.calls > after_first

    def test_a_truncated_lookup_still_answers_its_own_key(self, enabled_cache):
        api = Api(self._many_users())

        api.complete("user", completer=complete_user_filter)
        after_first = api.calls
        api.complete("user", completer=complete_user_filter)

        assert api.calls == after_first

    def test_a_complete_lookup_is_not_recorded_as_truncated(self, enabled_cache):
        Api(USERS).complete("son", completer=complete_user_filter)
        assert cache.get("users", "all|son")["truncated"] is False


class TestScope:
    """--assign must not offer a disabled account just because --author
    cached one, and vice versa."""

    def test_a_filter_lookup_does_not_serve_an_assignee_lookup(self, enabled_cache):
        api = Api(USERS)

        assert _values(api.complete("sven", completer=complete_user_filter)) == [
            "sven.retired"
        ]
        after_first = api.calls

        assert _values(api.complete("sven", completer=complete_user)) == []
        assert api.calls > after_first

    def test_an_assignee_lookup_does_not_serve_a_filter_lookup(self, enabled_cache):
        api = Api(USERS)

        assert _values(api.complete("sven", completer=complete_user)) == []
        after_first = api.calls

        assert _values(api.complete("sven", completer=complete_user_filter)) == [
            "sven.retired"
        ]
        assert api.calls > after_first

    def test_the_two_scopes_are_separate_entries(self, enabled_cache):
        api = Api(USERS)
        api.complete("sven", completer=complete_user)
        api.complete("sven", completer=complete_user_filter)

        assert cache.get("users", "enabled|sven") is not cache.MISS
        assert cache.get("users", "all|sven") is not cache.MISS

    def test_a_disabled_record_is_never_offered_for_assignment(self, enabled_cache):
        """Belt and braces: even handed a wider record set, complete_user
        filters disabled accounts out."""
        cache.set(
            "users",
            "enabled|sven",
            {
                "records": [
                    {"username": "sven.retired", "realName": "Sven", "disabled": True}
                ],
                "truncated": False,
            },
        )
        api = Api(USERS)
        assert _values(api.complete("sven", completer=complete_user)) == []
        assert api.calls == 0


class TestKeyNormalisation:
    def test_typed_case_does_not_split_the_cache(self, enabled_cache):
        api = Api(USERS)

        api.complete("son")
        after_first = api.calls
        result = api.complete("SON")

        assert api.calls == after_first
        assert _values(result) == ["SONJA.BERGSTROM"]

    def test_whitespace_only_text_shares_the_empty_entry(self, enabled_cache):
        api = Api(USERS)

        api.complete("", completer=complete_user_filter)
        after_first = api.calls
        api.complete("  ", completer=complete_user_filter)

        assert api.calls == after_first

    def test_the_list_filter_shares_entries_with_the_single_name_filter(
        self, enabled_cache
    ):
        """The comma-separated list completes each name from the same entry."""
        api = Api(USERS)

        api.complete("son", completer=complete_user_filter)
        after_first = api.calls
        result = api.complete("@me,son", completer=complete_user_list_filter)

        assert api.calls == after_first
        assert _values(result) == ["@me,sonja.bergstrom"]


class TestExpiryAndFailure:
    def test_a_stale_entry_is_looked_up_again(self, enabled_cache):
        api = Api(USERS)
        api.complete("son")
        after_first = api.calls

        with patch("time.time", return_value=time.time() + 10**7):
            api.complete("son")

        assert api.calls > after_first

    def test_an_unavailable_api_is_not_cached(self, enabled_cache):
        """Caching a failure would silence completion for a whole TTL."""
        api = Api(USERS)

        assert api.unavailable("son") == []
        assert cache.get("users", "enabled|son") is cache.MISS

        assert _values(api.complete("son")) == ["sonja.bergstrom"]

    def test_me_shortcut_touches_neither_cache_nor_api(self, enabled_cache):
        api = Api(USERS)

        assert _values(api.complete("@m")) == ["@me"]

        assert api.calls == 0
        assert cache.instance_dir() is None or not os.path.isdir(
            os.path.join(cache.instance_dir(), "users")
        )


class TestLookupCost:
    """Narrowing probes several keys, so resolving the cache location must not
    be repeated per probe - that costs more than the round trips it saves."""

    def test_a_completion_reads_the_configuration_once(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ) as read_config:
            api = Api(USERS)
            api.complete("", completer=complete_user_filter)
            read_config.reset_mock()

            api.complete("sonja", completer=complete_user_filter)

            assert read_config.call_count == 1

    def test_a_disabled_cache_reads_the_configuration_not_at_all(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "0")
        with patch("phabfive.core.Phabfive.read_config") as read_config:
            Api(USERS).complete("son")
        read_config.assert_not_called()


class TestDisabled:
    def test_every_tab_asks_the_api_when_caching_is_off(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "0")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            api = Api(USERS)
            api.complete("son")
            after_first = api.calls
            api.complete("son")
            assert api.calls > after_first


class TestStoredShape:
    def test_only_the_three_fields_completion_reads_are_stored(self, enabled_cache):
        Api(USERS).complete("son", completer=complete_user_filter)

        entry = cache.get("users", "all|son")
        for record in entry["records"]:
            assert set(record) == {"username", "realName", "disabled"}

    def test_no_phid_or_policy_reaches_the_disk(self, enabled_cache):
        Api(USERS).complete("", completer=complete_user_filter)

        namespace_dir = os.path.join(cache.instance_dir(), "users")
        for name in os.listdir(namespace_dir):
            with open(os.path.join(namespace_dir, name)) as stream:
                raw = stream.read()
            assert "PHID-USER" not in raw
            assert "roles" not in json.loads(raw)["value"]["records"][0]
