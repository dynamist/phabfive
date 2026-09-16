# -*- coding: utf-8 -*-
"""Tests for the generic on-disk cache."""

# python std lib
import json
import os
import re
import time
from unittest.mock import patch

# 3rd party imports
import pytest

# phabfive imports
from phabfive import cache
from tests.conftest import CONF


skip_on_windows = pytest.mark.skipif(
    os.name == "nt", reason="Windows uses ACLs, not Unix permission bits"
)
skip_as_root = pytest.mark.skipif(
    os.name != "nt" and os.geteuid() == 0,
    reason="root ignores the permission bits this asserts on",
)


def _names_the_instance(directory):
    """Whether a path is the cache directory for the CONF instance.

    The host is readable in the name so the directory can be recognised; the
    digest that follows keeps two instances apart and hides the token.
    """
    return bool(
        re.fullmatch(r"phorge\.example\.com-[0-9a-f]{8}", os.path.basename(directory))
    )


def _conf(**overrides):
    merged = dict(CONF)
    merged.update(overrides)
    return merged


class TestRoundTrip:
    def test_stores_and_returns_a_value(self, enabled_cache):
        cache.set("users", "key", {"a": 1})
        assert cache.get("users", "key") == {"a": 1}

    def test_absent_key_is_a_miss(self, enabled_cache):
        assert cache.get("users", "nothing-here") is cache.MISS

    @pytest.mark.parametrize("value", [[], None, 0, "", False, {"nested": [1, 2]}])
    def test_falsey_values_round_trip_and_are_not_a_miss(self, enabled_cache, value):
        cache.set("users", "key", value)
        assert cache.get("users", "key") == value
        assert cache.get("users", "key") is not cache.MISS

    def test_namespaces_do_not_collide(self, enabled_cache):
        cache.set("users", "key", "u")
        cache.set("projects", "key", "p")
        assert cache.get("users", "key") == "u"
        assert cache.get("projects", "key") == "p"

    @pytest.mark.parametrize(
        "key", ["../../etc/passwd", "sonja/bergström", "å" * 4000, "", "a b\tc\n"]
    )
    def test_awkward_keys_stay_inside_the_cache_root(self, enabled_cache, key):
        cache.set("users", key, "value")
        assert cache.get("users", key) == "value"

        directory = cache.instance_dir()
        path = cache._entry_path(directory, "users", key)
        assert os.path.realpath(path).startswith(os.path.realpath(directory))


class TestExpiry:
    def test_entry_older_than_the_ttl_is_a_miss(self, enabled_cache):
        cache.set("users", "key", "value")
        with patch("time.time", return_value=time.time() + 100000):
            assert cache.get("users", "key") is cache.MISS

    def test_entry_within_the_ttl_is_a_hit(self, enabled_cache):
        cache.set("users", "key", "value")
        with patch("time.time", return_value=time.time() + 60):
            assert cache.get("users", "key") == "value"

    def test_callers_ttl_wins_over_the_stored_one(self, enabled_cache):
        cache.set("users", "key", "value", ttl=86400)
        assert cache.get("users", "key", ttl=0) is cache.MISS

    def test_a_zero_ttl_expires_an_entry_written_this_instant(self, enabled_cache):
        """Not merely "older than ttl": on a coarse clock, as on Windows, an
        entry written and read in the same tick has an age of exactly 0."""
        with patch("time.time", return_value=1000.0):
            cache.set("users", "key", "value")
            assert cache.get("users", "key", ttl=0) is cache.MISS

    def test_an_entry_expires_the_moment_it_reaches_its_ttl(self, enabled_cache):
        with patch("time.time", return_value=1000.0):
            cache.set("users", "key", "value")

        with patch("time.time", return_value=1059.9):
            assert cache.get("users", "key", ttl=60) == "value"
        with patch("time.time", return_value=1060.0):
            assert cache.get("users", "key", ttl=60) is cache.MISS

    def test_ttl_override_applies_to_every_namespace(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config",
            return_value=(_conf(PHAB_CACHE_TTL=7), True),
        ):
            assert cache.ttl_for("users") == 7
            assert cache.ttl_for("projects") == 7

    def test_namespace_ttls_differ_by_default(self, enabled_cache):
        # Usernames are cached for far longer than projects
        assert cache.ttl_for("users") > cache.ttl_for("projects")
        assert cache.ttl_for("unknown-namespace") == cache.CACHE_TTL_DEFAULT


class TestCorruptionTolerance:
    def _entry_file(self):
        directory = cache.instance_dir()
        return cache._entry_path(directory, "users", "key")

    @pytest.mark.parametrize(
        "content",
        [
            b"not json at all",
            b"",
            b'{"version": 1, "created": 1}',  # no key, no value
            b'{"version": 1, "created": "yesterday", "key": "key", "value": 1}',
            b"\x00\x01\x02",
        ],
    )
    def test_unreadable_content_is_a_miss_not_an_error(self, enabled_cache, content):
        cache.set("users", "key", "value")
        with open(self._entry_file(), "wb") as stream:
            stream.write(content)
        assert cache.get("users", "key") is cache.MISS

    def test_entry_from_another_schema_version_is_a_miss(self, enabled_cache):
        cache.set("users", "key", "value")
        path = self._entry_file()
        with open(path) as stream:
            entry = json.load(stream)
        entry["version"] = cache.CACHE_SCHEMA_VERSION + 1
        with open(path, "w") as stream:
            json.dump(entry, stream)
        assert cache.get("users", "key") is cache.MISS

    def test_digest_collision_is_a_miss_not_a_wrong_answer(self, enabled_cache):
        """The key is checked inside the file, so a collision cannot mislead."""
        cache.set("users", "key", "value")
        path = self._entry_file()
        with open(path) as stream:
            entry = json.load(stream)
        entry["key"] = "a-different-key-that-hashed-the-same"
        with open(path, "w") as stream:
            json.dump(entry, stream)
        assert cache.get("users", "key") is cache.MISS

    @skip_on_windows
    @skip_as_root
    def test_unreadable_directory_is_a_miss(self, enabled_cache):
        cache.set("users", "key", "value")
        namespace_dir = os.path.join(cache.instance_dir(), "users")
        os.chmod(namespace_dir, 0o000)
        try:
            assert cache.get("users", "key") is cache.MISS
        finally:
            os.chmod(namespace_dir, 0o700)

    @skip_on_windows
    @skip_as_root
    def test_drifted_directory_permissions_are_restored(self, enabled_cache):
        """Writing repairs a cache directory that was left world-readable."""
        cache.set("users", "first", "value")
        namespace_dir = os.path.join(cache.instance_dir(), "users")
        os.chmod(namespace_dir, 0o755)

        cache.set("users", "second", "value")

        assert os.stat(namespace_dir).st_mode & 0o777 == 0o700
        assert cache.get("users", "second") == "value"

    def test_unwritable_root_does_not_raise(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PHAB_CACHE", "1")
        blocker = tmp_path / "a-file-not-a-dir"
        blocker.write_text("")
        monkeypatch.setenv("PHAB_CACHE_DIR", str(blocker))
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            cache.set("users", "key", "value")  # must not raise
            assert cache.get("users", "key") is cache.MISS

    def test_unreadable_config_disables_the_cache(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch("phabfive.core.Phabfive.read_config", side_effect=OSError("boom")):
            assert cache.instance_dir() is None
            cache.set("users", "key", "value")
            assert cache.get("users", "key") is cache.MISS


class TestPermissions:
    @skip_on_windows
    def test_directories_are_private(self, enabled_cache):
        cache.set("users", "key", "value")
        directory = cache.instance_dir()
        for path in [
            os.path.join(directory, "users"),
            directory,
            os.path.dirname(directory),
        ]:
            assert os.stat(path).st_mode & 0o777 == 0o700, path

    @skip_on_windows
    def test_entry_files_are_private(self, enabled_cache):
        cache.set("users", "key", "value")
        path = cache._entry_path(cache.instance_dir(), "users", "key")
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_no_temporary_files_are_left_behind(self, enabled_cache):
        cache.set("users", "key", "value")
        namespace_dir = os.path.join(cache.instance_dir(), "users")
        assert [n for n in os.listdir(namespace_dir) if n.endswith(".tmp")] == []


class TestInstanceIsolation:
    def _set_for(self, conf, key, value):
        with patch("phabfive.core.Phabfive.read_config", return_value=(conf, True)):
            cache.set("users", key, value)

    def _get_for(self, conf, key):
        with patch("phabfive.core.Phabfive.read_config", return_value=(conf, True)):
            return cache.get("users", key)

    def test_different_urls_do_not_share_entries(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        one = _conf(PHAB_URL="https://one.example.com/api/")
        two = _conf(PHAB_URL="https://two.example.com/api/")
        self._set_for(one, "key", "from-one")
        assert self._get_for(two, "key") is cache.MISS
        assert self._get_for(one, "key") == "from-one"

    def test_different_tokens_do_not_share_entries(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        one = _conf(PHAB_TOKEN="api-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        two = _conf(PHAB_TOKEN="api-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        self._set_for(one, "key", "from-one")
        assert self._get_for(two, "key") is cache.MISS

    def test_no_phab_url_means_no_caching(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config",
            return_value=(_conf(PHAB_URL=""), False),
        ):
            assert cache.instance_dir() is None

    def test_instance_directory_name_is_recognisable(self, enabled_cache):
        assert _names_the_instance(cache.instance_dir())

    def test_token_is_never_written_in_clear_text(self, enabled_cache):
        cache.set("users", "key", "value")
        for dirpath, _, files in os.walk(cache.root()):
            for name in files:
                with open(os.path.join(dirpath, name), "rb") as stream:
                    assert CONF["PHAB_TOKEN"].encode() not in stream.read()
        assert CONF["PHAB_TOKEN"] not in cache.instance_dir()


class TestDisabling:
    def test_env_var_disables_without_reading_config(self, monkeypatch):
        """The env gate must short-circuit before anything loads config."""
        monkeypatch.setenv("PHAB_CACHE", "0")
        with patch("phabfive.core.Phabfive.read_config") as read_config:
            assert cache.get("users", "key") is cache.MISS
            cache.set("users", "key", "value")
            assert cache.instance_dir() is None
        read_config.assert_not_called()

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "", "None"])
    def test_falsey_env_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("PHAB_CACHE", value)
        assert cache.enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
    def test_truthy_env_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("PHAB_CACHE", value)
        assert cache.enabled() is True

    def test_unset_env_var_leaves_the_cache_on(self, monkeypatch):
        monkeypatch.delenv("PHAB_CACHE", raising=False)
        assert cache.enabled() is True

    def test_config_can_disable_it_too(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config",
            return_value=(_conf(PHAB_CACHE=False), True),
        ):
            assert cache.instance_dir() is None
            cache.set("users", "key", "value")
            assert cache.get("users", "key") is cache.MISS


class TestContext:
    def test_resolves_location_and_ttl_in_one_read(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ) as read_config:
            directory, ttl = cache.context("users")

        assert read_config.call_count == 1
        assert _names_the_instance(directory)
        assert ttl == cache.CACHE_TTLS["users"]

    def test_honours_the_ttl_override(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config",
            return_value=(_conf(PHAB_CACHE_TTL=9), True),
        ):
            assert cache.context("users")[1] == 9

    def test_reports_nothing_to_cache_when_disabled(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "0")
        assert cache.context("users") == (None, 0)

    def test_matches_instance_dir_and_ttl_for(self, enabled_cache):
        assert cache.context("users") == (cache.instance_dir(), cache.ttl_for("users"))


class TestCachedCall:
    def test_produces_and_stores_on_a_miss(self, enabled_cache):
        calls = []

        def produce():
            calls.append(1)
            return ["value"]

        assert cache.cached_call("users", "key", produce) == ["value"]
        assert cache.cached_call("users", "key", produce) == ["value"]
        assert len(calls) == 1

    def test_a_failed_lookup_is_not_cached(self, enabled_cache):
        """None means the API was unavailable; caching it would silence
        completion for a whole TTL."""
        calls = []

        def produce():
            calls.append(1)
            return None

        assert cache.cached_call("users", "key", produce) is None
        assert cache.cached_call("users", "key", produce) is None
        assert len(calls) == 2


class TestClear:
    def test_removes_entries_and_counts_them(self, enabled_cache):
        cache.set("users", "one", 1)
        cache.set("users", "two", 2)
        cache.set("projects", "three", 3)

        assert cache.clear() == 3
        assert cache.get("users", "one") is cache.MISS

    def test_nothing_cached_is_not_an_error(self, enabled_cache):
        assert cache.clear() == 0

    def test_clears_entries_left_over_after_disabling_the_cache(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            cache.set("users", "key", "value")

        monkeypatch.setenv("PHAB_CACHE", "0")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            assert cache.clear() == 1

    def test_all_instances_works_without_credentials(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)
        ):
            cache.set("users", "key", "value")

        # No usable configuration at all - the case somebody hits when things
        # are broken and they reach for "cache clear --all"
        with patch("phabfive.core.Phabfive.read_config", side_effect=OSError("boom")):
            assert cache.clear(all_instances=True) == 1

    def test_single_instance_clear_reports_no_configured_instance(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config",
            return_value=(_conf(PHAB_URL=""), False),
        ):
            assert cache.clear() is None


class TestPruning:
    def test_a_namespace_stays_bounded(self, enabled_cache, monkeypatch):
        monkeypatch.setattr(cache, "MAX_ENTRIES_PER_NAMESPACE", 5)
        for i in range(20):
            cache.set("users", f"key{i}", i)

        namespace_dir = os.path.join(cache.instance_dir(), "users")
        assert len(os.listdir(namespace_dir)) <= 5

    def test_the_newest_entry_survives_pruning(self, enabled_cache, monkeypatch):
        monkeypatch.setattr(cache, "MAX_ENTRIES_PER_NAMESPACE", 5)
        for i in range(20):
            cache.set("users", f"key{i}", i)

        assert cache.get("users", "key19") == 19


class TestDescribe:
    def test_reports_namespaces_without_revealing_values(self, enabled_cache):
        cache.set("users", "key", [{"username": "sonja.bergstrom"}])

        described = cache.describe()

        assert described["Enabled"] is True
        assert "sonja.bergstrom" not in json.dumps(described)
        namespaces = {n["Namespace"]: n for n in described["Namespaces"]}
        assert namespaces["users"]["Entries"] == 1
        assert namespaces["users"]["Size"] > 0
        assert namespaces["users"]["TTL"] == cache.ttl_for("users")

    def test_explains_why_the_cache_is_off(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "0")
        described = cache.describe()
        assert described["Enabled"] is False
        assert "environment" in described["Reason"]

    def test_explains_a_missing_url(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "1")
        with patch(
            "phabfive.core.Phabfive.read_config",
            return_value=(_conf(PHAB_URL=""), False),
        ):
            assert cache.describe()["Reason"] == "no PHAB_URL is configured"
