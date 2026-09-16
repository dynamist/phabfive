# -*- coding: utf-8 -*-
"""Tests for the `phabfive cache` commands."""

# python std lib
import json
from unittest.mock import patch

# 3rd party imports
from typer.testing import CliRunner

# phabfive imports
from phabfive import cache
from tests.conftest import CONF
from phabfive.cli import app

runner = CliRunner()


class TestClear:
    def test_removes_entries_and_reports_how_many(self, enabled_cache):
        cache.set("users", "one", 1)
        cache.set("users", "two", 2)

        result = runner.invoke(app, ["cache", "clear"])

        assert result.exit_code == 0
        assert "Removed 2 cached entries" in result.stdout
        assert cache.get("users", "one") is cache.MISS

    def test_singular_for_one_entry(self, enabled_cache):
        cache.set("users", "one", 1)
        result = runner.invoke(app, ["cache", "clear"])
        assert "Removed 1 cached entry from" in result.stdout

    def test_an_empty_cache_is_not_an_error(self, enabled_cache):
        result = runner.invoke(app, ["cache", "clear"])
        assert result.exit_code == 0
        assert "Removed 0 cached entries" in result.stdout

    def test_all_works_without_credentials(self, monkeypatch):
        """What somebody reaches for when things are broken."""
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            cache.set("users", "one", 1)

        with patch("phabfive.core.Phabfive.read_config", side_effect=OSError("boom")):
            result = runner.invoke(app, ["cache", "clear", "--all"])

        assert result.exit_code == 0
        assert "Removed 1 cached entry from every instance" in result.stdout

    def test_says_so_when_no_instance_is_configured(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        conf = dict(CONF, PHAB_URL="")
        with patch("phabfive.core.Phabfive.read_config", return_value=(conf, False)):
            result = runner.invoke(app, ["cache", "clear"])

        assert result.exit_code == 1
        assert "No instance is configured" in result.stderr


class TestInfo:
    def test_reports_namespaces(self, enabled_cache):
        cache.set("users", "key", {"records": [], "truncated": False})

        result = runner.invoke(app, ["--format=rich", "cache", "info"])

        assert result.exit_code == 0
        assert "users" in result.stdout
        assert "enabled" in result.stdout

    def test_says_when_nothing_is_cached(self, enabled_cache):
        result = runner.invoke(app, ["--format=rich", "cache", "info"])
        assert "Nothing cached yet" in result.stdout

    def test_explains_why_the_cache_is_off(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "0")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            result = runner.invoke(app, ["--format=rich", "cache", "info"])

        assert result.exit_code == 0
        assert "off" in result.stdout
        assert "environment" in result.stdout

    def test_never_prints_a_cached_value(self, enabled_cache):
        cache.set(
            "users",
            "key",
            {
                "records": [
                    {
                        "username": "sonja.bergstrom",
                        "realName": "Sonja",
                        "disabled": False,
                    }
                ],
                "truncated": False,
            },
        )

        result = runner.invoke(app, ["cache", "info"])

        assert "sonja.bergstrom" not in result.stdout

    def test_json_output_parses(self, enabled_cache):
        cache.set("users", "key", {"records": [], "truncated": False})

        result = runner.invoke(app, ["--format=json", "cache", "info"])

        described = json.loads(result.stdout)
        assert described["Enabled"] is True
        assert described["Namespaces"][0]["Namespace"] == "users"

    def test_yaml_output_is_produced(self, enabled_cache):
        cache.set("users", "key", {"records": [], "truncated": False})

        result = runner.invoke(app, ["--format=yaml", "cache", "info"])

        assert result.exit_code == 0
        assert "Namespace: users" in result.stdout

    def test_does_not_need_a_working_connection(self, monkeypatch):
        """Inspecting the cache must work when the instance is unreachable."""
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch("phabfive.core.Phabfive.read_config", side_effect=OSError("boom")):
            result = runner.invoke(app, ["cache", "info"])

        assert result.exit_code == 0
