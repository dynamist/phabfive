# -*- coding: utf-8 -*-

"""Placing a new task in a Space.

Searching may name several Spaces at once, and happily resolves a wildcard to
all of them. Creating a task in one cannot: there is a single `space`
transaction and it takes a single PHID, so anything that names more than one
Space has to be refused rather than silently resolved to the first match.

That includes two Spaces sharing a name, which the search resolver answers with
whichever it enumerated first -- tolerable for a filter, not for deciding where
a task is stored.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import PhabfiveConfigException, PhabfiveRemoteException
from phabfive.maniphest.core import Maniphest
from phabfive.maniphest.resolvers import resolve_space


def _space(number, name=None):
    """One entry shaped the way phid.lookup returns them."""
    name = name or f"Space {number}"
    return {
        "phid": f"PHID-SPCE-{number}",
        "name": name,
        "fullName": name,
        "uri": f"/S{number}",
    }


def _phab(visible):
    """A client that can see exactly `visible`, and nothing else."""
    spaces = {f"S{n}": _space(n, nm) for n, nm in visible.items()}

    def lookup(names):
        return {name: spaces[name] for name in names if name in spaces}

    phab = MagicMock()
    phab.phid.lookup.side_effect = lookup
    return phab


class TestNamingOneSpace:
    """A monogram, a name, or a pattern that leaves no doubt."""

    @pytest.mark.parametrize(
        "given, expected",
        [
            ("S3", "PHID-SPCE-3"),
            ("s3", "PHID-SPCE-3"),
            ("Archive", "PHID-SPCE-10"),
            ("archive", "PHID-SPCE-10"),
            ("*rch*", "PHID-SPCE-10"),
        ],
    )
    def test_it_resolves_to_that_space(self, given, expected):
        phab = _phab({1: "Default", 3: "Management Team", 10: "Archive"})

        assert resolve_space(phab, given)["phid"] == expected

    def test_a_monogram_costs_one_lookup(self):
        # The whole range is probed only to match a name or a wildcard; a
        # monogram names one Space outright.
        phab = _phab({1: "Default", 3: "Management Team", 10: "Archive"})

        resolve_space(phab, "S3")

        asked = [call.kwargs["names"] for call in phab.phid.lookup.call_args_list]
        assert asked == [["S3"]]

    def test_the_monogram_comes_back_with_the_space(self):
        phab = _phab({1: "Default", 10: "Archive"})

        resolved = resolve_space(phab, "Archive")

        assert resolved["monogram"] == "S10"
        assert resolved["name"] == "Archive"


class TestMoreThanOneSpace:
    """What filtering tolerates, placing a task must refuse."""

    def test_a_pattern_matching_two_spaces_is_refused(self):
        phab = _phab({1: "Default", 3: "Archive stage 1", 10: "Archive stage 2"})

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_space(phab, "Archive*")

        message = str(excinfo.value)
        assert "ambiguous" in message
        # Both candidates are named, so the monogram to use next is right there
        assert "S3 (Archive stage 1)" in message
        assert "S10 (Archive stage 2)" in message

    def test_matching_everything_is_refused(self):
        phab = _phab({1: "Default", 10: "Archive"})

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_space(phab, "*")

        assert "ambiguous" in str(excinfo.value)

    def test_a_name_two_spaces_share_is_refused(self):
        # The search resolver answers this with whichever it enumerated first.
        phab = _phab({3: "Archive", 10: "Archive"})

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_space(phab, "Archive")

        message = str(excinfo.value)
        assert "ambiguous" in message
        assert "S3 (Archive)" in message
        assert "S10 (Archive)" in message

    def test_a_pattern_matching_one_of_two_lookalikes_is_allowed(self):
        phab = _phab({1: "Default", 3: "Archive stage 1", 10: "Archive stage 2"})

        assert resolve_space(phab, "*stage 2")["phid"] == "PHID-SPCE-10"


class TestNoSuchSpace:
    """The error says what is there, as the search resolver does."""

    def test_an_unknown_space_lists_the_visible_ones(self):
        phab = _phab({1: "Default", 3: "Management Team", 10: "Archive"})

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_space(phab, "S2")

        assert "Available spaces: S1, S3, S10" in str(excinfo.value)

    def test_an_instance_without_spaces_says_so(self):
        phab = _phab({})

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_space(phab, "S1")

        assert "no visible spaces" in str(excinfo.value)

    def test_a_failing_lookup_is_not_reported_as_not_found(self):
        phab = MagicMock()
        phab.phid.lookup.side_effect = Exception("boom")

        with pytest.raises(PhabfiveRemoteException) as excinfo:
            resolve_space(phab, "Archive")

        assert "boom" in str(excinfo.value)
        assert "not found" not in str(excinfo.value)


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestCreatingInASpace:
    """What `maniphest create --space` puts on the wire."""

    def _maniphest(self, phab, conf=None):
        maniphest = Maniphest()
        maniphest.phab = phab
        maniphest.url = "https://phorge.example.com/"
        maniphest.conf = conf if conf is not None else {"PHAB_SPACE": "S1"}

        phab.maniphest.edit.return_value = {"object": {"id": 5, "phid": "PHID-TASK-5"}}
        maniphest.get_task_info = MagicMock(
            return_value=(None, {"uri": "https://phorge.example.com/T5"})
        )
        return maniphest

    def _transactions(self, phab):
        return phab.maniphest.edit.call_args.kwargs["transactions"]

    def test_the_task_is_created_in_the_named_space(self, mock_init):
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)

        maniphest.create_task(title="Task in the archive", space="Archive")

        assert {"type": "space", "value": "PHID-SPCE-10"} in self._transactions(phab)

    def test_without_a_space_the_server_decides(self, mock_init):
        # PHAB_SPACE narrows searches. Honouring it here would silently move
        # where tasks land, on an instance whose default Space is not S1.
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab, conf={"PHAB_SPACE": "S10"})

        maniphest.create_task(title="Task")

        types = [t["type"] for t in self._transactions(phab)]
        assert "space" not in types

    def test_an_ambiguous_space_creates_nothing(self, mock_init):
        phab = _phab({3: "Archive", 10: "Archive"})
        maniphest = self._maniphest(phab)

        with pytest.raises(PhabfiveConfigException):
            maniphest.create_task(title="Task", space="Archive")

        phab.maniphest.edit.assert_not_called()

    def test_a_dry_run_names_the_space_it_resolved(self, mock_init):
        # A wildcard is worth echoing back as the Space it picked, since that
        # is the part the user did not type.
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)

        result = maniphest.create_task(title="Task", space="*rch*", dry_run=True)

        assert result["space"] == "S10 (Archive)"
        phab.maniphest.edit.assert_not_called()

    def test_a_dry_run_still_rejects_an_unknown_space(self, mock_init):
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)

        with pytest.raises(PhabfiveConfigException):
            maniphest.create_task(title="Task", space="S2", dry_run=True)


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestCreatingFromATemplate:
    """`create --with` places tasks the same way the flag does."""

    def _maniphest(self, phab):
        maniphest = Maniphest()
        maniphest.phab = phab
        maniphest.url = "https://phorge.example.com/"
        maniphest.conf = {"PHAB_SPACE": "S1"}
        phab.user.search.return_value = {"data": []}
        phab.project.search.return_value = {"data": []}
        phab.maniphest.edit.return_value = {"object": {"id": 5, "phid": "PHID-TASK-5"}}
        return maniphest

    def _template(self, tmp_path, body):
        config = tmp_path / "tasks.yaml"
        config.write_text("variables: {}\ntasks:\n" + body)
        return str(config)

    def test_a_space_in_the_template_places_the_task(self, mock_init, tmp_path):
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)
        config = self._template(
            tmp_path,
            "  - title: Archive the old plans\n"
            "    description: Somewhere out of the way\n"
            "    space: Archive\n",
        )

        maniphest.create_tasks_from_yaml(config)

        transactions = phab.maniphest.edit.call_args.kwargs["transactions"]
        assert {"type": "space", "value": "PHID-SPCE-10"} in transactions

    def test_every_task_naming_it_shares_one_resolution(self, mock_init, tmp_path):
        # A template puts a whole batch of tasks in one Space; resolving it per
        # task would re-probe the instance for each of them.
        def probes_for(body):
            phab = _phab({1: "Default", 10: "Archive"})
            config = self._template(tmp_path, body)
            self._maniphest(phab).create_tasks_from_yaml(config)
            return phab.phid.lookup.call_count

        one = "  - title: One\n    description: x\n    space: Archive\n"
        two = one + "  - title: Two\n    description: y\n    space: Archive\n"

        assert probes_for(two) == probes_for(one)

    def test_an_ambiguous_space_creates_nothing(self, mock_init, tmp_path):
        # Resolution happens while the template is pre-processed, before any
        # task is committed.
        phab = _phab({3: "Archive", 10: "Archive"})
        maniphest = self._maniphest(phab)
        config = self._template(
            tmp_path,
            "  - title: One\n    description: x\n    space: Archive\n",
        )

        with pytest.raises(PhabfiveConfigException):
            maniphest.create_tasks_from_yaml(config)

        phab.maniphest.edit.assert_not_called()
