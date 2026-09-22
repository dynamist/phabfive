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

        result = runner.invoke(app, ["--format=rich", "cache", "clear"])

        assert result.exit_code == 0
        assert "Removed 2 cached lookups" in result.stdout
        assert cache.get("users", "one") is cache.MISS

    def test_singular_for_one_entry(self, enabled_cache):
        cache.set("users", "one", 1)
        result = runner.invoke(app, ["--format=rich", "cache", "clear"])
        assert "Removed 1 cached lookup from" in result.stdout

    def test_an_empty_cache_is_not_an_error(self, enabled_cache):
        result = runner.invoke(app, ["--format=rich", "cache", "clear"])
        assert result.exit_code == 0
        assert "Removed 0 cached lookups" in result.stdout

    def test_all_works_without_credentials(self, monkeypatch):
        """What somebody reaches for when things are broken."""
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            cache.set("users", "one", 1)

        with patch("phabfive.core.Phabfive.read_config", side_effect=OSError("boom")):
            result = runner.invoke(app, ["--format=rich", "cache", "clear", "--all"])

        assert result.exit_code == 0
        assert "Removed 1 cached lookup from every instance" in result.stdout

    def test_says_so_when_no_instance_is_configured(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        conf = dict(CONF, PHAB_URL="")
        with patch("phabfive.core.Phabfive.read_config", return_value=(conf, False)):
            result = runner.invoke(app, ["--format=rich", "cache", "clear"])

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

    def test_table_shows_how_many_records_an_entry_holds(self, enabled_cache):
        """One entry holding many users should not read as one user."""
        cache.set(
            "users",
            "key",
            {
                "records": [{"username": f"user{i}"} for i in range(29)],
                "truncated": False,
            },
        )

        result = runner.invoke(app, ["--format=rich", "cache", "info"])

        assert result.exit_code == 0
        row = next(line for line in result.stdout.splitlines() if "users" in line)
        cells = [cell.strip() for cell in row.strip("│").split("│")]
        # One entry, but it holds 29 users
        assert cells[1] == "1"
        assert cells[2] == "29"

    def test_table_shows_a_dash_for_uncountable_entries(self, enabled_cache):
        """Unknown must not render as 0, which would read as "holds nothing"."""
        cache.set("users", "key", "not a list of records")

        result = runner.invoke(app, ["--format=rich", "cache", "info"])

        assert result.exit_code == 0
        row = next(line for line in result.stdout.splitlines() if "users" in line)
        cells = [cell.strip() for cell in row.strip("│").split("│")]
        # Namespace, Lookups, Records, ...
        assert cells[1] == "1"
        assert cells[2] == "-"

    def test_mentions_accounts_cached_under_another_token(self, enabled_cache):
        """ "Nothing cached yet" is misleading when the host has entries."""
        other = dict(CONF, PHAB_TOKEN="api-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        with patch("phabfive.core.Phabfive.read_config", return_value=(other, True)):
            cache.set("projects", "key", {"records": [1], "truncated": False})

        result = runner.invoke(app, ["--format=rich", "cache", "info"])

        assert result.exit_code == 0
        assert "Nothing cached yet" in result.stdout
        assert "1 other cached account" in result.stdout

    def test_quiet_when_no_other_account_cached_this_host(self, enabled_cache):
        cache.set("projects", "key", {"records": [1], "truncated": False})

        result = runner.invoke(app, ["--format=rich", "cache", "info"])

        assert "other cached account" not in result.stdout

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


class TestHumanSize:
    def test_units(self):
        from phabfive.cli.cache import _human_size

        assert _human_size(0) == "0 B"
        assert _human_size(1023) == "1023 B"
        assert _human_size(1024) == "1.0 KB"
        assert _human_size(1536) == "1.5 KB"
        assert _human_size(1024 * 1024) == "1.0 MB"
        # MB is the largest unit, however large the size
        assert _human_size(5 * 1024**3) == "5120.0 MB"
