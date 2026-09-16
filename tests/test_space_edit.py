# -*- coding: utf-8 -*-

"""Moving a task between Spaces with `maniphest edit --space`.

The transaction is the same one `create` uses, so the resolver is shared and
refuses anything naming more than one Space. What is particular to editing is
that the task is already somewhere: the move has to be shown as a change from
that Space, and a task already in the target is not a change at all.

`--space` also has to count as an edit option. Without that, `edit T123
--space=S3` looks like an edit with no options given, which opens $EDITOR on
the description instead of moving anything.
"""

from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import PhabfiveConfigException
from phabfive.maniphest.core import Maniphest


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


def _task(task_id="123", space_phid="PHID-SPCE-1"):
    """A task as maniphest.search returns it for editing."""
    return {
        "id": int(task_id),
        "phid": f"PHID-TASK-{task_id}",
        "fields": {
            "name": "A task",
            "priority": {"value": 50, "name": "Normal"},
            "status": {"value": "open", "name": "Open"},
            "ownerPHID": None,
            "description": {"raw": ""},
            "spacePHID": space_phid,
        },
        "attachments": {
            "columns": {"boards": {}},
            "projects": {"projectPHIDs": []},
            "subscribers": {"subscriberPHIDs": []},
        },
    }


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestMovingATask:
    """What `edit --space` puts on the wire, and what it reports."""

    def _maniphest(self, phab, task=None):
        maniphest = Maniphest()
        maniphest.phab = phab
        maniphest.url = "https://phorge.example.com/"
        maniphest.conf = {"PHAB_SPACE": "S1"}
        maniphest._get_task_data = MagicMock(return_value=task or _task())
        return maniphest

    def _transactions(self, phab):
        return phab.maniphest.edit.call_args.kwargs["transactions"]

    def test_the_task_moves_to_the_named_space(self, mock_init):
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)

        maniphest.edit_task_by_id(task_id="123", space="Archive")

        assert {"type": "space", "value": "PHID-SPCE-10"} in self._transactions(phab)

    def test_the_change_says_which_space_it_left(self, mock_init):
        # Enumerating to resolve the target also names the Space being left,
        # so both ends of the move are shown without a lookup of their own.
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)

        result = maniphest.edit_task_by_id(task_id="123", space="S10")

        assert result["changes"] == [
            {"field": "Space", "old": "S1 (Default)", "new": "S10 (Archive)"}
        ]

    def test_a_task_already_there_is_left_alone(self, mock_init):
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab, task=_task(space_phid="PHID-SPCE-10"))

        result = maniphest.edit_task_by_id(task_id="123", space="Archive")

        assert result["changes"] == []
        phab.maniphest.edit.assert_not_called()

    def test_an_ambiguous_space_moves_nothing(self, mock_init):
        phab = _phab({3: "Archive", 10: "Archive"})
        maniphest = self._maniphest(phab)

        with pytest.raises(PhabfiveConfigException):
            maniphest.edit_task_by_id(task_id="123", space="Archive")

        phab.maniphest.edit.assert_not_called()

    def test_a_dry_run_applies_nothing(self, mock_init):
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)

        result = maniphest.edit_task_by_id(task_id="123", space="S10", dry_run=True)

        assert result["dry_run"] is True
        phab.maniphest.edit.assert_not_called()

    def test_a_space_past_the_probe_ceiling_is_still_named(self, mock_init):
        # phid.lookup is only probed up to SPACE_PROBE_MAX, so a task sitting
        # in a Space above that is not among the enumerated ones. Reporting
        # that as "(none)" would say the task had been in no Space at all.
        phab = _phab({1: "Default", 10: "Archive"})
        phab.phid.query.return_value = {
            "PHID-SPCE-500": {"name": "Deep storage", "uri": "/S500"}
        }
        maniphest = self._maniphest(phab, task=_task(space_phid="PHID-SPCE-500"))

        result = maniphest.edit_task_by_id(task_id="123", space="S10")

        assert result["changes"][0]["old"] == "S500 (Deep storage)"

    def test_several_tasks_share_one_probe(self, mock_init):
        # A batch resolves the same Space once per task; the enumeration
        # behind it must not run again for each one.
        phab = _phab({1: "Default", 10: "Archive"})
        maniphest = self._maniphest(phab)

        maniphest.edit_task_by_id(task_id="123", space="Archive")
        calls_after_first = phab.phid.lookup.call_count
        maniphest.edit_task_by_id(task_id="124", space="Archive")

        assert phab.phid.lookup.call_count == calls_after_first


@pytest.fixture
def mock_config(monkeypatch):
    """Configuration and a client that need no network."""
    monkeypatch.setenv("PHAB_TOKEN", "cli-testtoken1234567890123456789")
    monkeypatch.setenv("PHAB_URL", "https://phabricator.example.com/api/")
    monkeypatch.setattr("phabfive.core.Phabricator", lambda **kwargs: MagicMock())


class TestSpaceCountsAsAnEditOption:
    """`--space` on its own is an edit, not an invitation to open $EDITOR."""

    def test_it_does_not_open_the_editor(self, mock_config):
        from phabfive.edit import Edit

        edit_app = Edit()
        with mock.patch.object(Edit, "_edit_task_single", return_value=0) as single:
            edit_app.edit_objects(object_id="T123", space="S3")

        assert single.call_args.kwargs["space"] == "S3"
        assert single.call_args.kwargs["edit_description_in_editor"] is False

    def test_it_reaches_the_batch_path(self, mock_config):
        # Several tasks go through edit_tasks_batch, which has a signature of
        # its own: a flag missing from it is dropped without a word.
        from phabfive.edit import Edit

        edit_app = Edit()
        with mock.patch("phabfive.edit.core.edit_tasks_batch", return_value=0) as batch:
            edit_app.edit_objects(object_id="T123,T124", space="S3")

        assert batch.call_args.kwargs["space"] == "S3"
