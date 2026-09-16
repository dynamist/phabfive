# -*- coding: utf-8 -*-
"""Tests for the priority and status completion lookups.

These drive the real _get_values_with_api_fallback rather than patching it
out, because what they are about is what happens when the API misbehaves -
and that is exactly the part a patched helper skips.
"""

# 3rd party imports
import pytest
from unittest.mock import MagicMock, patch

# phabfive imports
from phabfive.cli.completers import (
    DEFAULT_PRIORITY_VALUES,
    DEFAULT_STATUS_VALUES,
    _get_priorities,
    _get_statuses,
)
from phabfive.maniphest.fetchers import get_api_priority_names


def _connected(phab):
    """Patch Phabfive so the completion path gets this client, not a socket."""
    client = MagicMock()
    client.phab = phab
    return patch("phabfive.core.Phabfive", return_value=client)


def _priorities(*pairs):
    """Build a maniphest.priority.search answer in the shape Phorge sends.

    Flat records with name, keywords, short, color and value - not the
    fields/attachments shape the *.search endpoints use.
    """
    return {
        "data": [
            {
                "name": name,
                "keywords": list(keywords),
                "short": name,
                "color": "blue",
                "value": 50,
            }
            for name, *keywords in pairs
        ]
    }


class TestPriorityFetch:
    def test_reads_the_keyword_rather_than_the_displayed_name(self):
        """The keyword is what a task is edited with, and "Needs Triage" is
        "triage" - not something the displayed name can be reduced to."""
        phab = MagicMock()
        phab.maniphest.priority.search.return_value = _priorities(
            ("Unbreak Now!", "unbreak"),
            ("Needs Triage", "triage"),
            ("Wishlist", "wish", "wishlist"),
        )

        assert get_api_priority_names(phab) == ["unbreak", "triage", "wish"]

    def test_a_broken_lookup_raises_rather_than_inventing_priorities(self):
        """Its caller falls back; a caller that remembers must not be lied to."""
        phab = MagicMock()
        phab.maniphest.priority.search.side_effect = RuntimeError("no such method")

        with pytest.raises(RuntimeError):
            get_api_priority_names(phab)


class TestCompletionFallback:
    def test_a_broken_priority_lookup_still_offers_the_defaults(self):
        phab = MagicMock()
        phab.maniphest.priority.search.side_effect = RuntimeError("no such method")

        with _connected(phab):
            assert _get_priorities() == DEFAULT_PRIORITY_VALUES

    def test_a_broken_status_lookup_still_offers_the_defaults(self):
        phab = MagicMock()
        phab.maniphest.querystatuses.side_effect = RuntimeError("no such method")

        with _connected(phab):
            assert _get_statuses() == DEFAULT_STATUS_VALUES

    def test_a_working_priority_lookup_is_preferred(self):
        phab = MagicMock()
        phab.maniphest.priority.search.return_value = _priorities(
            ("Blocker", "blocker"), ("Normal", "normal")
        )

        with _connected(phab):
            assert _get_priorities() == ["blocker", "normal"]

    def test_a_working_status_lookup_is_preferred(self):
        phab = MagicMock()
        phab.maniphest.querystatuses.return_value = {
            "statusMap": {"open": "Open", "onhold": "On Hold"}
        }

        with _connected(phab):
            assert _get_statuses() == ["open", "onhold"]
