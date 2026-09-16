# -*- coding: utf-8 -*-

"""Completing a Space, and remembering the answer.

Spaces have no search endpoint: the only way to find them is to ask
phid.lookup about S1, S2, S3... which costs two requests over the whole range.
That is far too slow to repeat on every tab, so this is the completion the
`spaces` cache namespace exists for.

What is offered has to resolve. A monogram always does; a name does unless
several Spaces share it, in which case only the monograms are offered -- the
same reasoning as an ambiguous project name, since completing to a shared name
would earn nothing but the ambiguity error.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from phabfive import cache
from phabfive.cli.completers import (
    _get_spaces,
    complete_space,
    complete_space_filter,
)
from tests.conftest import CONF


def _space(number, name):
    return {
        "phid": f"PHID-SPCE-{number}",
        "name": name,
        "fullName": name,
        "uri": f"/S{number}",
    }


class Api:
    """A fake instance whose Spaces are known, that counts the probes."""

    def __init__(self, visible=None, broken=False):
        spaces = {f"S{n}": _space(n, nm) for n, nm in (visible or {}).items()}

        def lookup(names):
            return {name: spaces[name] for name in names if name in spaces}

        self.phab = MagicMock()
        if broken:
            self.phab.phid.lookup.side_effect = RuntimeError("no route")
        else:
            self.phab.phid.lookup.side_effect = lookup

    @property
    def probes(self):
        return self.phab.phid.lookup.call_count

    def complete(self, complete_func=None, incomplete=""):
        client = MagicMock()
        client.phab = self.phab
        with patch("phabfive.core.Phabfive") as phabfive:
            phabfive.return_value = client
            # The class itself is replaced, so the classmethod the cache reads
            # its configuration through has to be answered here too
            phabfive.read_config.return_value = (dict(CONF), True)
            if complete_func is None:
                return _get_spaces()
            return complete_func(incomplete)


SEEDED = {1: "Default", 3: "Restricted", 10: "Archive"}


class TestWhatIsOffered:
    """Every completion has to be something the resolver accepts."""

    def test_nothing_typed_offers_the_monograms(self):
        # The names ride along as descriptions, which zsh and fish show, so
        # the shorter list loses nothing.
        assert Api(SEEDED).complete(complete_space) == [
            ("S1", "Default"),
            ("S3", "Restricted"),
            ("S10", "Archive"),
        ]

    def test_a_monogram_prefix_narrows_to_it(self):
        assert Api(SEEDED).complete(complete_space, "S1") == [
            ("S1", "Default"),
            ("S10", "Archive"),
        ]

    def test_a_name_can_be_typed_instead(self):
        assert Api(SEEDED).complete(complete_space, "Arch") == [("Archive", "S10")]

    def test_a_name_completes_in_the_case_it_was_typed(self):
        # The shell replaces the whole word, and the resolver matches names
        # case-insensitively, so following the typed case is what lets bash
        # see the completion as an extension of it.
        assert Api(SEEDED).complete(complete_space, "arch") == [("archive", "S10")]

    def test_a_name_two_spaces_share_is_not_offered(self):
        # Completing to it would only earn "Space 'Archive' is ambiguous".
        offered = Api({3: "Archive", 10: "Archive"}).complete(complete_space, "Arch")

        assert offered == []

    def test_their_monograms_are_offered_instead(self):
        offered = Api({3: "Archive", 10: "Archive"}).complete(complete_space, "S")

        assert offered == [("S3", "Archive"), ("S10", "Archive")]

    def test_an_unreachable_instance_offers_nothing(self):
        # There is no such thing as a default Space list: an invented one
        # would complete to a value that cannot resolve.
        assert Api(broken=True).complete(complete_space, "S") == []


class TestFilterCompletion:
    """maniphest search --space takes a comma-separated list."""

    def test_only_the_space_after_the_last_comma_is_completed(self):
        assert Api(SEEDED).complete(complete_space_filter, "S1,Arch") == [
            ("S1,Archive", "S10")
        ]

    def test_a_wildcard_is_left_alone(self):
        assert Api(SEEDED).complete(complete_space_filter, "*rch*") == []


class TestTheCache:
    """Two requests per tab is what the namespace is here to avoid."""

    def test_a_second_tab_asks_the_instance_nothing(self, enabled_cache):
        api = Api(SEEDED)
        assert api.complete() == [
            ["S1", "Default"],
            ["S3", "Restricted"],
            ["S10", "Archive"],
        ]
        after_first = api.probes

        api.complete()

        assert api.probes == after_first

    def test_the_answer_is_written_to_disk(self, enabled_cache):
        Api(SEEDED).complete()

        assert cache.get("spaces", "all") == [
            ["S1", "Default"],
            ["S3", "Restricted"],
            ["S10", "Archive"],
        ]

    def test_a_stale_entry_is_probed_again(self, enabled_cache):
        api = Api(SEEDED)
        api.complete()
        after_first = api.probes

        with patch("time.time", return_value=time.time() + 10**7):
            api.complete()

        assert api.probes > after_first

    def test_the_namespace_is_fresh_for_a_day(self, enabled_cache):
        """A namespace typo would silently fall back to the default TTL."""
        assert cache.ttl_for("spaces") == 86400

    def test_a_failed_probe_is_not_remembered(self, enabled_cache):
        # fetch_all_spaces raises rather than returning what it managed to
        # find, so a failure cannot be written down as an empty instance.
        assert Api(broken=True).complete() == []

        assert cache.get("spaces", "all") is cache.MISS

    def test_a_failed_probe_is_retried_on_the_next_tab(self, enabled_cache):
        Api(broken=True).complete()

        assert Api(SEEDED).complete() != []

    def test_every_tab_probes_when_caching_is_off(self, monkeypatch):
        monkeypatch.setenv("PHAB_CACHE", "0")
        api = Api(SEEDED)
        api.complete()
        after_first = api.probes

        api.complete()

        assert api.probes > after_first


def _offered(args, incomplete):
    """What shell completion offers for a value, through the real CLI."""
    import typer
    from click.shell_completion import ShellComplete

    from phabfive.cli import app

    command = typer.main.get_command(app)
    completion = ShellComplete(command, {}, "phabfive", "_PHABFIVE_COMPLETE")

    return [item.value for item in completion.get_completions(args, incomplete)]


@pytest.mark.parametrize(
    "args",
    [
        ["maniphest", "create", "--space"],
        ["maniphest", "edit", "T1", "--space"],
        ["edit", "T1", "--space"],
        ["maniphest", "search", "--space"],
    ],
    ids=["create", "maniphest edit", "edit", "search"],
)
def test_every_space_option_completes(args):
    """An option wired up without its completer fails silently, offering files."""
    spaces = [["S1", "Default"], ["S10", "Archive"]]

    with patch("phabfive.cli.completers._get_spaces", return_value=spaces):
        assert _offered(args, "Arch") == ["Archive"]
