# -*- coding: utf-8 -*-

"""Clearing every account cached for a host.

The cache directory is named from the URL and the token together, so
`cache clear` could only find the directory belonging to the exact token
that was configured. A token that did not match reported "Removed 0
cached entries" and exited 0, which is indistinguishable from there
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
from phabfive.cli.completers import complete_cached_host
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

        result = runner.invoke(app, ["cache", "clear", "--url", "phorge.example.com"])

        assert result.exit_code == 0
        assert "Removed 2 cached entries" in result.stdout
        assert _dirs() == []

    def test_rejects_a_value_naming_no_host(self, enabled_cache):
        result = runner.invoke(app, ["cache", "clear", "--url", ""])

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

        result = runner.invoke(app, ["cache", "clear"])

        assert result.exit_code == 0
        assert "Removed 0 cached entries" in result.stdout
        assert "under a different token" in result.stderr
        assert "--url" in result.stderr

    def test_quiet_when_the_configured_token_matched(self, enabled_cache):
        _write_entry(CONF["PHAB_URL"], CONF["PHAB_TOKEN"])

        result = runner.invoke(app, ["cache", "clear"])

        assert "Removed 1 cached entry" in result.stdout
        assert "different token" not in result.stderr

    def test_quiet_when_the_host_has_nothing_cached(self, enabled_cache):
        result = runner.invoke(app, ["cache", "clear"])

        assert "Removed 0 cached entries" in result.stdout
        assert "different token" not in result.stderr
