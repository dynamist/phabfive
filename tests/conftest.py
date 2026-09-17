# -*- coding: utf-8 -*-
"""Shared pytest configuration.

The completion cache is on by default, so without this every test run would
read and write the developer's real cache directory: tests would get hits,
skip their mocked fetches and fail depending on what ran before them. The
fixture below turns the cache off and points it somewhere disposable for
every test; the cache tests opt back in with the enabled_cache fixture.
"""

# python std lib
import os
from unittest.mock import patch

# 3rd party imports
import pytest


# Tests against a live Phorge in the k3d cluster only run when asked for, see
# `make test-k8s` and `make test-e2e`
if not os.environ.get("PHABFIVE_LIVE_TESTS"):
    collect_ignore = ["k8s", "e2e"]


CONF = {
    "PHAB_URL": "https://phorge.example.com/api/",
    "PHAB_TOKEN": "api-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "PHAB_CACHE": True,
    "PHAB_CACHE_TTL": 0,
    "PHAB_CACHE_DIR": "",
}


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    """Keep every test away from the real cache directory."""
    monkeypatch.setenv("PHAB_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PHAB_CACHE", "0")


@pytest.fixture
def enabled_cache(monkeypatch):
    """Switch the cache on, with configuration that needs no real files."""
    monkeypatch.setenv("PHAB_CACHE", "1")
    with patch("phabfive.core.Phabfive.read_config", return_value=(dict(CONF), True)):
        yield
