# -*- coding: utf-8 -*-

"""How Spaces are discovered, and why it cannot guess where the numbering ends.

There is no `spaces.search` endpoint, so the only way to find Spaces is to ask
`phid.lookup` about monograms. That lookup is policy-filtered: a Space the
viewer cannot see is omitted from the answer exactly as though it had never
existed. Visible monograms are therefore sparse and arbitrary -- a user with
access to S1 and S90 and nothing between them is ordinary, not exotic.

This used to be probed one monogram per request, stopping after five misses in
a row, which cut those users' Space lists short and made `--space='*'` quietly
skip Spaces. `phid.lookup` takes a list, so the whole range goes in one request
and nothing has to guess where to stop.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import PhabfiveConfigException, PhabfiveRemoteException
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.resolvers import (
    SPACE_PROBE_CHUNK,
    SPACE_PROBE_MAX,
    fetch_all_spaces,
    resolve_space_phids,
)


def _space(number, name=None):
    """One entry shaped the way phid.lookup returns them."""
    name = name or f"Space {number}"
    return {
        "phid": f"PHID-SPCE-{number}",
        "name": name,
        "fullName": name,
        "uri": f"/S{number}",
    }


def _phab(visible, response_for_none=None):
    """A client that can see exactly `visible`, and nothing else.

    `visible` maps a monogram number to its entry. Anything not in it is
    omitted from the answer, which is what both a non-existent Space and a
    Space the viewer lacks permission for look like on the wire.
    """
    spaces = {f"S{n}": _space(n, nm) for n, nm in visible.items()}

    def lookup(names):
        found = {name: spaces[name] for name in names if name in spaces}
        if not found and response_for_none is not None:
            return response_for_none
        return found

    phab = MagicMock()
    phab.phid.lookup.side_effect = lookup
    return phab


def _names_asked(phab):
    """Every monogram the client was asked about, across all calls."""
    asked = []
    for call in phab.phid.lookup.call_args_list:
        asked.extend(call.kwargs["names"])
    return asked


class TestPermissionGaps:
    """A gap in the visible monograms must not end the search."""

    def test_space_after_a_gap_is_found(self):
        # The regression: the old five-miss rule stopped at S7 and never saw S10.
        phab = _phab({1: "Global", 2: "Secret", 10: "Archive"})

        phids = resolve_space_phids(phab, "*")

        assert phids == ["PHID-SPCE-1", "PHID-SPCE-2", "PHID-SPCE-10"]

    def test_only_distant_spaces_are_visible(self):
        # What a restricted viewer sees: two Spaces, 88 invisible ones between.
        phab = _phab({1: "Global", 90: "Restricted"})

        phids = resolve_space_phids(phab, "*")

        assert phids == ["PHID-SPCE-1", "PHID-SPCE-90"]

    def test_the_available_list_names_every_visible_space(self):
        # The "not found" message is a suggestion list, so a truncated probe
        # makes it misleading rather than merely incomplete.
        phab = _phab({1: "Global", 10: "Archive"})

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_space_phids(phab, "Nope")

        assert "S1" in str(excinfo.value)
        assert "S10" in str(excinfo.value)

    def test_nothing_stops_early_on_what_was_found(self):
        # Whatever is visible, the same range is asked about: no rule may
        # shorten the probe based on the answers so far.
        sparse = _phab({1: "Global"})
        dense = _phab({n: f"Space {n}" for n in range(1, 40)})

        resolve_space_phids(sparse, "*")
        resolve_space_phids(dense, "*")

        assert _names_asked(sparse) == _names_asked(dense)


class TestRequestCount:
    """One request for the range, not one per monogram."""

    def test_enumeration_is_a_single_request(self):
        phab = _phab({1: "Global", 2: "Secret", 10: "Archive"})

        resolve_space_phids(phab, "*")

        assert phab.phid.lookup.call_count == SPACE_PROBE_MAX // SPACE_PROBE_CHUNK

    def test_the_whole_range_is_asked_about(self):
        phab = _phab({1: "Global"})

        fetch_all_spaces(phab)

        assert _names_asked(phab) == [f"S{i}" for i in range(1, SPACE_PROBE_MAX + 1)]

    def test_an_exact_monogram_skips_enumeration(self):
        # The common path, including every search that does not pass --space.
        phab = _phab({1: "Global", 2: "Secret"})

        phids = resolve_space_phids(phab, "S1")

        assert phids == ["PHID-SPCE-1"]
        assert _names_asked(phab) == ["S1"]

    def test_an_exact_monogram_works_beyond_the_probe_range(self):
        # Asking by monogram needs no enumeration, so no ceiling applies.
        beyond = SPACE_PROBE_MAX + 500
        phab = _phab({beyond: "Distant"})

        phids = resolve_space_phids(phab, f"S{beyond}")

        assert phids == [f"PHID-SPCE-{beyond}"]
        assert _names_asked(phab) == [f"S{beyond}"]

    def test_an_unknown_monogram_falls_back_to_enumeration(self):
        # Only so the error can list what the viewer can actually see.
        phab = _phab({1: "Global"})

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_space_phids(phab, "S404")

        assert "Available spaces: S1" in str(excinfo.value)

    def test_prefetched_spaces_are_not_looked_up_again(self):
        phab = _phab({1: "Global"})
        all_spaces = fetch_all_spaces(phab)
        phab.phid.lookup.reset_mock()

        resolve_space_phids(phab, "*", all_spaces=all_spaces)

        phab.phid.lookup.assert_not_called()


class TestFailuresAreNotMissingSpaces:
    """An API error must not read as 'there is no Space there'."""

    def test_a_failing_lookup_raises(self):
        phab = MagicMock()
        phab.phid.lookup.side_effect = Exception("boom")

        with pytest.raises(PhabfiveRemoteException) as excinfo:
            resolve_space_phids(phab, "*")

        message = str(excinfo.value)
        assert "boom" in message
        # It must not be reported as the Space simply not existing.
        assert "not found" not in message

    def test_a_later_failure_does_not_return_a_partial_list(self):
        # Half a Space list looks exactly like a complete one to the caller,
        # so a failure partway through has to abort rather than truncate.
        first = {f"S{n}": _space(n) for n in range(1, SPACE_PROBE_CHUNK + 1)}
        calls = []

        def lookup(names):
            calls.append(names)
            if len(calls) == 1:
                return {n: first[n] for n in names if n in first}
            raise Exception("boom")

        phab = MagicMock()
        phab.phid.lookup.side_effect = lookup

        with pytest.raises(PhabfiveRemoteException):
            fetch_all_spaces(phab)

    def test_a_failing_lookup_is_not_reported_as_an_empty_instance(self, caplog):
        phab = MagicMock()
        phab.phid.lookup.side_effect = Exception("boom")

        with pytest.raises(PhabfiveRemoteException):
            resolve_space_phids(phab, "*")

        assert "No spaces found" not in caplog.text


class TestEmptyInstance:
    """No visible Spaces is an answer, not a failure."""

    @pytest.mark.parametrize("empty", [{}, []], ids=["record", "list"])
    def test_an_empty_answer_yields_no_spaces(self, empty, caplog):
        # Phorge sends back a JSON array, not an object, when nothing matches,
        # so the empty case arrives as a list and must not crash.
        phab = _phab({}, response_for_none=empty)

        assert resolve_space_phids(phab, "S1") == []
        assert "No spaces found" in caplog.text

    def test_a_wildcard_over_an_empty_instance_yields_no_spaces(self):
        phab = _phab({}, response_for_none=[])

        assert resolve_space_phids(phab, "*") == []


class TestExistingMockShape:
    """The suite's other mocks answer every question with S1."""

    def test_a_mock_ignoring_its_argument_still_yields_only_s1(self):
        # ~22 tests set phid.lookup.return_value = {"S1": ...}, returned no
        # matter which names are asked for. Accepting only the names actually
        # requested is what keeps those honest.
        phab = MagicMock()
        phab.phid.lookup.return_value = {"S1": _space(1, "Global")}

        assert resolve_space_phids(phab, "S1") == ["PHID-SPCE-1"]
        assert resolve_space_phids(phab, "*") == ["PHID-SPCE-1"]


class TestPatternMatching:
    """Matching behaviour is unchanged."""

    @pytest.mark.parametrize(
        "pattern, expected",
        [
            ("S*", ["PHID-SPCE-1", "PHID-SPCE-2"]),
            ("*global*", ["PHID-SPCE-1"]),
            ("Global", ["PHID-SPCE-1"]),
            ("s1", ["PHID-SPCE-1"]),
            ("Secret", ["PHID-SPCE-2"]),
        ],
    )
    def test_patterns_resolve_as_before(self, pattern, expected):
        phab = _phab({1: "Global", 2: "Secret"})

        assert resolve_space_phids(phab, pattern) == expected


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestOneProbePerCommand:
    """Naming several Spaces must not re-probe for each one."""

    def _maniphest(self, phab, conf=None):
        maniphest = Maniphest()
        maniphest.phab = phab
        maniphest.url = "https://phabricator.example.com"
        maniphest.conf = conf if conf is not None else {"PHAB_SPACE": "S1"}

        response = MagicMock()
        response.response = {"data": []}
        response.get.return_value = {"after": None}
        phab.maniphest.search.return_value = response

        maniphest._resolve_project_phids = MagicMock(return_value=[])
        maniphest._get_open_statuses = MagicMock(return_value=["open"])
        return maniphest

    def test_several_named_spaces_share_one_probe(self, mock_init):
        phab = _phab({1: "Global", 2: "Secret"})
        self._maniphest(phab).task_search(space="S1,S2")

        # Two exact monograms: one small lookup each, and no enumeration.
        assert _names_asked(phab) == ["S1", "S2"]

    def test_repeated_searches_share_one_probe(self, mock_init):
        # A --with template runs task_search once per document.
        phab = _phab({1: "Global", 2: "Secret", 10: "Archive"})
        maniphest = self._maniphest(phab)

        maniphest.task_search(space="*")
        calls_after_first = phab.phid.lookup.call_count
        maniphest.task_search(space="*")

        assert phab.phid.lookup.call_count == calls_after_first
