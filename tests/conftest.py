# -*- coding: utf-8 -*-
"""Shared pytest configuration.

The completion cache is on by default, so without this every test run would
read and write the developer's real cache directory: tests would get hits,
skip their mocked fetches and fail depending on what ran before them. The
fixture below turns the cache off and points it somewhere disposable for
every test; the cache tests opt back in with PHAB_CACHE=1.
"""

# 3rd party imports
import pytest


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    """Keep every test away from the real cache directory."""
    monkeypatch.setenv("PHAB_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PHAB_CACHE", "0")
