# -*- coding: utf-8 -*-
"""Tests for caching of project (tag) completion.

The fixtures mirror tests/test_tag_completion.py; what these add is the
question that file cannot ask: how many times the API was actually called.
"""

# python std lib
import os
import time
from unittest.mock import patch

# 3rd party imports
import pytest

# phabfive imports
from phabfive import cache
from phabfive.cli import completers
from phabfive.cli.completers import TAG_COMPLETION_LIMIT, complete_tag
from tests.conftest import CONF
from tests.test_tag_completion import PROJECTS, _phab, _project


class Api:
    """A fake API client that remembers how often it was asked."""

    def __init__(self, projects, page_size=100):
        self.phab = _phab(projects, page_size=page_size)

    @property
    def calls(self):
        return self.phab.project.search.call_count

    def complete(self, incomplete):
        with patch.object(
            completers,
            "_get_values_with_api_fallback",
            side_effect=lambda fetch, default: fetch(self.phab),
        ):
            return complete_tag(incomplete)

    def uncached(self, incomplete):
        """Complete as if the cache had never been switched on."""
        with patch.object(cache, "enabled", return_value=False):
            return self.complete(incomplete)

    def unavailable(self, incomplete):
        """Complete while the API cannot be reached at all."""
        with patch.object(
            completers,
            "_get_values_with_api_fallback",
            side_effect=lambda fetch, default: default,
        ):
            return complete_tag(incomplete)


def _values(completions):
    return [c[0] if isinstance(c, tuple) else c for c in completions]


def _namespace_dir():
    return os.path.join(cache.instance_dir(), "projects")


class TestRepeatedCompletion:
    def test_second_tab_on_the_same_word_asks_the_api_nothing(self, enabled_cache):
        api = Api(PROJECTS)
        assert _values(api.complete("Kan")) == ["Kanban Board"]
        after_first = api.calls

        assert _values(api.complete("Kan")) == ["Kanban Board"]

        assert api.calls == after_first

    def test_the_lookup_is_actually_written_to_disk(self, enabled_cache):
        Api(PROJECTS).complete("Kan")

        entry = cache.get("projects", "kan")
        assert entry is not cache.MISS
        assert entry["records"] == [{"id": 1, "name": "Kanban Board", "parent": ""}]

    def test_what_is_stored_is_what_the_server_said(self, enabled_cache):
        """Not what was offered: the entry has to stay a superset.

        A search for "Core" finds "GUNNAR-Core" server-side even though the
        completion offers nothing, and the entry keeps it - otherwise the
        entry could not answer a prefix that does extend to it.
        """
        assert Api(PROJECTS).complete("Core") == []

        entry = cache.get("projects", "core")
        assert [record["name"] for record in entry["records"]] == ["GUNNAR-Core"]

    def test_a_cold_cache_still_asks_the_api(self, enabled_cache):
        api = Api(PROJECTS)

        api.complete("Kan")

        assert api.calls > 0


class TestNarrowing:
    def test_a_longer_prefix_is_served_from_a_shorter_lookup(self, enabled_cache):
        api = Api(PROJECTS)
        api.complete("Ka")
        after_first = api.calls

        assert _values(api.complete("Kanb")) == ["Kanban Board"]

        assert api.calls == after_first

    def test_one_empty_lookup_serves_every_prefix(self, enabled_cache):
        api = Api(PROJECTS)
        api.complete("")
        after_first = api.calls

        for incomplete in ["K", "Ka", "Kan", "Q", "Rel", "Spr"]:
            api.complete(incomplete)

        assert api.calls == after_first

    @pytest.mark.parametrize(
        "incomplete",
        ["", "k", "Kan", "q", "QA", "spr", "Sprint 1", "rel", "core", "gun", "zzz"],
    )
    def test_narrowed_answers_match_the_uncached_ones(self, enabled_cache, incomplete):
        api = Api(PROJECTS)
        expected = api.uncached(incomplete)
        after_uncached = api.calls

        Api(PROJECTS).complete("")  # a different client, so the cache is the only link

        assert api.complete(incomplete) == expected
        assert api.calls == after_uncached, "the answer came from the network"

    def test_a_narrowed_lookup_is_not_written_back(self, enabled_cache):
        api = Api(PROJECTS)
        api.complete("")

        api.complete("Kan")

        assert len(os.listdir(_namespace_dir())) == 1

    def test_a_word_prefix_match_that_is_not_a_name_prefix_is_not_offered(
        self, enabled_cache
    ):
        """The cached superset holds GUNNAR-Core, and "Core" must not offer it."""
        api = Api(PROJECTS)
        api.complete("")
        after_first = api.calls

        assert api.complete("Core") == []

        assert api.calls == after_first


def _many_projects(count):
    return [_project(index, f"Team {index:04d}") for index in range(count)]


class TestTruncation:
    def test_a_truncated_lookup_is_recorded_as_such(self, enabled_cache):
        Api(_many_projects(TAG_COMPLETION_LIMIT + 100)).complete("Team")

        assert cache.get("projects", "team")["truncated"] is True

    def test_a_truncated_lookup_cannot_be_narrowed(self, enabled_cache):
        api = Api(_many_projects(TAG_COMPLETION_LIMIT + 100))
        api.complete("Team")
        after_first = api.calls

        api.complete("Team 00")

        assert api.calls > after_first

    def test_a_truncated_lookup_still_answers_its_own_key(self, enabled_cache):
        api = Api(_many_projects(TAG_COMPLETION_LIMIT + 100))
        expected = api.complete("Team")
        after_first = api.calls

        assert api.complete("Team") == expected

        assert api.calls == after_first

    def test_a_complete_lookup_is_not_recorded_as_truncated(self, enabled_cache):
        Api(PROJECTS).complete("Kan")

        assert cache.get("projects", "kan")["truncated"] is False


class TestKeyNormalisation:
    def test_typed_case_does_not_split_the_cache(self, enabled_cache):
        api = Api(PROJECTS)
        api.complete("kan")
        after_first = api.calls

        assert _values(api.complete("KAN")) == ["KANBAN BOARD"]

        assert api.calls == after_first

    def test_whitespace_only_text_shares_the_empty_entry(self, enabled_cache):
        """_fetch_projects_named drops the constraint for either one."""
        api = Api(PROJECTS)
        api.complete("")
        after_first = api.calls

        api.complete("   ")

        assert api.calls == after_first
        assert len(os.listdir(_namespace_dir())) == 1


class TestExpiryAndFailure:
    def test_a_stale_entry_is_looked_up_again(self, enabled_cache):
        api = Api(PROJECTS)
        api.complete("Kan")
        after_first = api.calls

        with patch("time.time", return_value=time.time() + 10**7):
            api.complete("Kan")

        assert api.calls > after_first

    def test_an_unavailable_api_is_not_cached(self, enabled_cache):
        api = Api(PROJECTS)

        assert api.unavailable("Kan") == []

        assert cache.get("projects", "kan") is cache.MISS
        assert _values(api.complete("Kan")) == ["Kanban Board"]

    def test_an_instance_without_the_project_is_cached(self, enabled_cache):
        """An empty answer is an answer, unlike a failure, so it is kept."""
        api = Api(PROJECTS)
        assert api.complete("zzz") == []
        after_first = api.calls

        api.complete("zzz")

        assert api.calls == after_first


class TestLookupCost:
    """Narrowing probes several keys, so resolving the cache location must not
    be repeated per probe - that costs more than the round trips it saves."""

    def test_a_completion_reads_the_configuration_once(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ) as read_config:
            api = Api(PROJECTS)
            api.complete("")
            read_config.reset_mock()

            api.complete("Kanban")

            assert read_config.call_count == 1


class TestDisabled:
    def test_every_tab_asks_the_api_when_caching_is_off(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "0")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            api = Api(PROJECTS)
            api.complete("Kan")
            after_first = api.calls
            api.complete("Kan")
            assert api.calls > after_first


class TestStoredShape:
    def test_only_the_three_fields_completion_reads_are_stored(self, enabled_cache):
        Api(PROJECTS).complete("")

        for record in cache.get("projects", "")["records"]:
            assert set(record) == {"id", "name", "parent"}

    def test_no_phid_reaches_the_disk(self, enabled_cache):
        Api(PROJECTS).complete("")

        for name in os.listdir(_namespace_dir()):
            with open(os.path.join(_namespace_dir(), name)) as stream:
                assert "PHID-PROJ" not in stream.read()

    def test_the_namespace_matches_the_configured_ttl(self, enabled_cache):
        """A namespace typo would silently fall back to the default TTL."""
        assert cache.ttl_for("projects") == 300
