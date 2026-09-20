# -*- coding: utf-8 -*-

"""Clearing every account cached for a host.

The cache directory is named from the URL and the token together, so
`cache clear` could only find the directory belonging to the exact token
that was configured. A token that did not match reported "Removed 0
cached lookups" and exited 0, which is indistinguishable from there
being nothing to clear.

Destroying an instance invalidates what was cached for all of its
accounts, so clearing by host is both the correct scope and the one that
needs no token to work out which directories those are.
"""

# python std lib
import os
from unittest.mock import patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive import cache
from phabfive.cli import app
from phabfive.cli.completers import complete_cache_namespace, complete_cached_host
from tests.conftest import CONF

runner = CliRunner()

OTHER_TOKEN = "api-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
OTHER_HOST = "https://elsewhere.example.com/api/"


def _write_entry(url, token, namespace="projects"):
    """Cache one entry as the given account would."""
    conf = dict(CONF, PHAB_URL=url, PHAB_TOKEN=token)
    with patch("phabfive.core.Phabfive.read_config", return_value=(conf, True)):
        cache.set(namespace, "key", {"records": [1], "truncated": False})


def _dirs():
    base = cache.root(dict(CONF))
    return sorted(os.listdir(base)) if os.path.isdir(base) else []


class TestClearHost:
    def test_clears_every_token_for_that_host(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])
        _write_entry(CONF["PHAB_URL"], OTHER_TOKEN)
        assert len(_dirs()) == 2

        removed = cache.clear_host(CONF["PHAB_URL"])

        assert removed == 2
        assert _dirs() == []

    def test_leaves_other_hosts_alone(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])
        _write_entry(OTHER_HOST, CONF["PHAB_TOKEN"])

        cache.clear_host(CONF["PHAB_URL"])

        assert len(_dirs()) == 1
        assert _dirs()[0].startswith("elsewhere.example.com-")

    @pytest.mark.parametrize(
        "form",
        [
            "phorge.example.com",
            "https://phorge.example.com",
            "https://phorge.example.com/api/",
        ],
    )
    def test_accepts_a_bare_host_or_a_url(self, enabled_cache, form):
        """Completion can only offer bare hosts, so those must work."""
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])

        assert cache.clear_host(form) == 1

    def test_nothing_cached_is_not_an_error(self, enabled_cache):
        assert cache.clear_host(CONF["PHAB_URL"]) == 0

    @pytest.mark.parametrize("value", ["", None])
    def test_a_value_naming_no_host_is_rejected(self, enabled_cache, value):
        assert cache.clear_host(value) is None


class TestCachedHosts:
    def test_lists_each_host_once(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])
        _write_entry(CONF["PHAB_URL"], OTHER_TOKEN)
        _write_entry(OTHER_HOST, CONF["PHAB_TOKEN"])

        assert cache.cached_hosts() == [
            "elsewhere.example.com",
            "phorge.example.com",
        ]

    def test_empty_when_nothing_is_cached(self, enabled_cache):
        assert cache.cached_hosts() == []


class TestCompleteCachedHost:
    def test_offers_cached_hosts(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])
        _write_entry(OTHER_HOST, CONF["PHAB_TOKEN"])

        assert complete_cached_host("") == [
            "elsewhere.example.com",
            "phorge.example.com",
        ]

    def test_narrows_by_what_was_typed(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])
        _write_entry(OTHER_HOST, CONF["PHAB_TOKEN"])

        assert complete_cached_host("pho") == ["phorge.example.com"]
        assert complete_cached_host("zz") == []

    @pytest.mark.parametrize(
        "typed", ["", "pho", "https://", "https://pho", "http://pho"]
    )
    def test_every_completion_starts_with_what_was_typed(self, enabled_cache, typed):
        """typer drops completions that do not, so a bare host would vanish."""
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])

        offered = complete_cached_host(typed)

        assert offered
        assert all(value.startswith(typed) for value in offered)

    def test_keeps_the_scheme_that_was_typed(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])

        assert complete_cached_host("https://pho") == ["https://phorge.example.com"]

    def test_no_cache_offers_nothing(self, enabled_cache):
        assert complete_cached_host("") == []


class TestClearUrlCommand:
    def test_clears_the_host(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])
        _write_entry(CONF["PHAB_URL"], OTHER_TOKEN)

        result = runner.invoke(
            app, ["--format=rich", "cache", "clear", "--url", "phorge.example.com"]
        )

        assert result.exit_code == 0
        assert "Removed 2 cached lookups" in result.stdout
        assert _dirs() == []

    def test_rejects_a_value_naming_no_host(self, enabled_cache):
        result = runner.invoke(app, ["--format=rich", "cache", "clear", "--url", ""])

        assert result.exit_code == 1
        assert "does not name a host" in result.stderr

    def test_rejects_url_together_with_all(self, enabled_cache):
        result = runner.invoke(
            app, ["cache", "clear", "--url", "phorge.example.com", "--all"]
        )

        assert result.exit_code == 1
        assert "cannot be combined" in result.stderr


class TestTokenMismatchIsVisible:
    def test_says_so_when_another_token_cached_this_host(self, enabled_cache):
        """Removing nothing must not read as "there was nothing to clear"."""
        _write_entry(CONF["PHAB_URL"], OTHER_TOKEN)

        result = runner.invoke(app, ["--format=rich", "cache", "clear"])

        assert result.exit_code == 0
        assert "Removed 0 cached lookups" in result.stdout
        assert "under a different token" in result.stderr
        assert "--url" in result.stderr

    def test_quiet_when_the_configured_token_matched(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])

        result = runner.invoke(app, ["--format=rich", "cache", "clear"])

        assert "Removed 1 cached lookup" in result.stdout
        assert "different token" not in result.stderr

    def test_quiet_when_the_host_has_nothing_cached(self, enabled_cache):
        result = runner.invoke(app, ["--format=rich", "cache", "clear"])

        assert "Removed 0 cached lookups" in result.stdout
        assert "different token" not in result.stderr


class TestClearNamespaces:
    """`cache clear users` clears one namespace rather than everything."""

    def _seed(self, url=None, token=None):
        conf = dict(
            CONF,
            PHAB_URL=url or CONF["PHAB_URL"],
            PHAB_TOKEN=token or CONF["PHAB_TOKEN"],
        )
        with patch("phabfive.core.Phabfive.read_config", return_value=(conf, True)):
            for namespace in ("projects", "users", "priorities"):
                cache.set(namespace, "key", {"records": [1], "truncated": False})

    def _namespaces(self):
        return sorted(n["Namespace"] for n in cache.describe()["Namespaces"])

    def test_clears_only_the_named_namespace(self, enabled_cache):
        self._seed()

        assert cache.clear(namespaces=["users"]) == 1
        assert self._namespaces() == ["priorities", "projects"]

    def test_clears_several_namespaces(self, enabled_cache):
        self._seed()

        assert cache.clear(namespaces=["users", "priorities"]) == 2
        assert self._namespaces() == ["projects"]

    def test_no_namespaces_clears_everything(self, enabled_cache):
        self._seed()

        assert cache.clear() == 3
        assert self._namespaces() == []

    def test_a_namespace_with_nothing_cached_is_not_an_error(self, enabled_cache):
        self._seed()

        assert cache.clear(namespaces=["spaces"]) == 0
        assert self._namespaces() == ["priorities", "projects", "users"]

    def test_host_scoped_clear_takes_namespaces(self, enabled_cache):
        """One namespace, across every account cached for the host."""
        self._seed()
        self._seed(token=OTHER_TOKEN)

        assert cache.clear_host(CONF["PHAB_URL"], namespaces=["users"]) == 2

        remaining = sorted(
            name
            for directory in cache._dirs_for_host(dict(CONF), CONF["PHAB_URL"])
            for name in os.listdir(directory)
        )
        assert remaining == ["priorities", "priorities", "projects", "projects"]

    def test_all_instances_clear_takes_namespaces(self, enabled_cache):
        """A namespace lives inside each instance, not beside them."""
        self._seed()
        self._seed(url=OTHER_HOST)

        assert cache.clear(all_instances=True, namespaces=["projects"]) == 2
        assert self._namespaces() == ["priorities", "users"]


class TestClearNamespacesCommand:
    def _seed(self, enabled_cache):
        for namespace in ("projects", "users"):
            cache.set(namespace, "key", {"records": [1], "truncated": False})

    def test_clears_the_named_namespace(self, enabled_cache):
        self._seed(enabled_cache)

        result = runner.invoke(app, ["--format=rich", "cache", "clear", "users"])

        assert result.exit_code == 0
        assert "(users)" in result.stdout
        assert [n["Namespace"] for n in cache.describe()["Namespaces"]] == ["projects"]

    def test_rejects_an_unknown_namespace(self, enabled_cache):
        """A typo must not clear nothing and report success."""
        self._seed(enabled_cache)

        result = runner.invoke(app, ["--format=rich", "cache", "clear", "userz"])

        assert result.exit_code == 1
        assert "unknown cache namespace: userz" in result.stderr
        assert "users" in result.stderr
        assert len(cache.describe()["Namespaces"]) == 2

    def test_rejects_an_unknown_namespace_among_known_ones(self, enabled_cache):
        self._seed(enabled_cache)

        result = runner.invoke(
            app, ["--format=rich", "cache", "clear", "users", "nope"]
        )

        assert result.exit_code == 1
        assert len(cache.describe()["Namespaces"]) == 2

    def test_repeating_a_namespace_names_it_once(self, enabled_cache):
        self._seed(enabled_cache)

        result = runner.invoke(
            app, ["--format=rich", "cache", "clear", "users", "users"]
        )

        assert result.exit_code == 0
        assert "(users)" in result.stdout

    def test_combines_with_url(self, enabled_cache):
        self._seed(enabled_cache)

        result = runner.invoke(
            app,
            ["--format=rich", "cache", "clear", "--url", "phorge.example.com", "users"],
        )

        assert result.exit_code == 0
        assert "(users)" in result.stdout
        assert [n["Namespace"] for n in cache.describe()["Namespaces"]] == ["projects"]


class TestCompleteCacheNamespace:
    def test_offers_every_known_namespace(self, enabled_cache):
        offered = [value for value, _ in complete_cache_namespace("")]

        assert offered == cache.known_namespaces()

    def test_narrows_by_what_was_typed(self, enabled_cache):
        offered = [value for value, _ in complete_cache_namespace("us")]

        assert offered == ["users"]
        assert complete_cache_namespace("zz") == []

    def test_describes_what_is_cached(self, enabled_cache):
        cache.set("users", "key", {"records": [1, 2, 3], "truncated": False})

        described = dict(complete_cache_namespace(""))

        assert described["users"] == "1 lookup, 3 records"
        assert described["projects"] == "nothing cached"

    def test_every_completion_starts_with_what_was_typed(self, enabled_cache):
        for typed in ["", "p", "us", "stat"]:
            assert all(
                value.startswith(typed) for value, _ in complete_cache_namespace(typed)
            )
