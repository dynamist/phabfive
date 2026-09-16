# -*- coding: utf-8 -*-
"""Tests for caching of the priority and status completion lookups.

Unlike users and projects these are whole instance-wide lists with nothing to
key on, so there is no narrowing to check - only that an answer is kept, that
a non-answer is not, and that the namespaces match their configured TTL.
"""

# python std lib
import time
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest

# phabfive imports
from phabfive import cache
from tests.conftest import CONF
from phabfive.cli.completers import (
    DEFAULT_PRIORITY_VALUES,
    DEFAULT_STATUS_VALUES,
    _get_priorities,
    _get_statuses,
)


class Api:
    """A fake API client that remembers how often it was asked."""

    def __init__(self, phab, complete):
        self.phab = phab
        self._complete = complete

    def complete(self):
        client = MagicMock()
        client.phab = self.phab
        with patch("phabfive.core.Phabfive") as phabfive:
            phabfive.return_value = client
            # The class itself is replaced, so the classmethod the cache reads
            # its configuration through has to be answered here too
            phabfive.read_config.return_value = (dict(CONF), True)
            return self._complete()

    def unavailable(self):
        """Complete while the instance cannot be connected to at all."""
        with patch("phabfive.core.Phabfive") as phabfive:
            phabfive.side_effect = RuntimeError("no route")
            phabfive.read_config.return_value = (dict(CONF), True)
            return self._complete()


def _priority_api(*keywords, broken=False):
    phab = MagicMock()
    if broken:
        phab.maniphest.priority.search.side_effect = RuntimeError("no such method")
    else:
        phab.maniphest.priority.search.return_value = {
            "data": [
                {"name": keyword.title(), "keywords": [keyword]} for keyword in keywords
            ]
        }
    return Api(phab, _get_priorities)


def _status_api(*keys, broken=False):
    phab = MagicMock()
    if broken:
        phab.maniphest.querystatuses.side_effect = RuntimeError("no such method")
    else:
        phab.maniphest.querystatuses.return_value = {
            "statusMap": {key: key.title() for key in keys}
        }
    return Api(phab, _get_statuses)


def _calls(api):
    if api._complete is _get_priorities:
        return api.phab.maniphest.priority.search.call_count
    return api.phab.maniphest.querystatuses.call_count


NAMESPACES = [
    ("priorities", _priority_api, ("blocker", "normal"), ["blocker", "normal"]),
    ("statuses", _status_api, ("open", "onhold"), ["open", "onhold"]),
]


@pytest.mark.parametrize("namespace, make_api, answer, expected", NAMESPACES)
class TestValueCache:
    def test_a_second_tab_asks_the_api_nothing(
        self, enabled_cache, namespace, make_api, answer, expected
    ):
        api = make_api(*answer)
        assert api.complete() == expected
        after_first = _calls(api)

        assert api.complete() == expected

        assert _calls(api) == after_first

    def test_the_answer_is_written_to_disk(
        self, enabled_cache, namespace, make_api, answer, expected
    ):
        make_api(*answer).complete()

        assert cache.get(namespace, "all") == expected

    def test_a_stale_entry_is_looked_up_again(
        self, enabled_cache, namespace, make_api, answer, expected
    ):
        api = make_api(*answer)
        api.complete()
        after_first = _calls(api)

        with patch("time.time", return_value=time.time() + 10**7):
            api.complete()

        assert _calls(api) > after_first

    def test_every_tab_asks_the_api_when_caching_is_off(
        self, monkeypatch, namespace, make_api, answer, expected
    ):
        monkeypatch.setenv("PHAB_CACHE", "0")
        api = make_api(*answer)
        api.complete()
        after_first = _calls(api)

        api.complete()

        assert _calls(api) > after_first

    def test_the_namespace_is_fresh_for_a_week(
        self, enabled_cache, namespace, make_api, answer, expected
    ):
        """A namespace typo would silently fall back to the default TTL."""
        assert cache.ttl_for(namespace) == 604800


@pytest.mark.parametrize(
    "namespace, make_api, default",
    [
        ("priorities", _priority_api, DEFAULT_PRIORITY_VALUES),
        ("statuses", _status_api, DEFAULT_STATUS_VALUES),
    ],
)
class TestNonAnswers:
    def test_a_broken_endpoint_does_not_cache_the_defaults(
        self, enabled_cache, namespace, make_api, default
    ):
        """Caching a guess would pin it for a week, which is the whole risk."""
        assert make_api(broken=True).complete() == default

        assert cache.get(namespace, "all") is cache.MISS

    def test_an_unreachable_instance_does_not_cache_the_defaults(
        self, enabled_cache, namespace, make_api, default
    ):
        assert make_api(broken=True).unavailable() == default

        assert cache.get(namespace, "all") is cache.MISS

    def test_an_empty_answer_is_not_cached(
        self, enabled_cache, namespace, make_api, default
    ):
        """No instance has zero priorities or zero statuses, so it is a failure."""
        assert make_api().complete() == default

        assert cache.get(namespace, "all") is cache.MISS

    def test_a_broken_endpoint_is_retried_rather_than_remembered(
        self, enabled_cache, namespace, make_api, default
    ):
        make_api(broken=True).complete()

        api = make_api("blocker") if namespace == "priorities" else make_api("open")
        assert api.complete() != default
