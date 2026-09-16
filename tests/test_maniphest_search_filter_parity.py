# -*- coding: utf-8 -*-

"""Parity tests for the three search paths of `maniphest search`.

`task_search` builds its filter constraints in three separate blocks,
one per code path:

* no tag, or ``--tag '*'``  -> ``constraints = {}``
* multiple projects         -> ``constraints = {"projects": [phid]}``, per project
* a single project          -> ``constraints = {"projects": project_phids}``

Each block then applies the same secondary filters by hand. Nothing
stops the three from drifting, so a filter added or fixed in one path
would silently not apply in the others, e.g. ``--tag A`` filtering by
assignee while ``--tag A+B`` does not.

These tests drive all three paths with identical arguments and require
the resulting constraints to match, ignoring the project selection
itself.
"""

from unittest.mock import MagicMock, patch

from phabfive.maniphest.core import Maniphest

PROJECT_A = "PHID-PROJ-aaaaaaaaaaaaaaaaaaaa"
PROJECT_B = "PHID-PROJ-bbbbbbbbbbbbbbbbbbbb"
USER_PHID = "PHID-USER-cccccccccccccccccccc"

# One value for every secondary filter the blocks apply, so a filter
# that is missing from one path shows up as a difference.
FILTERS = {
    "text_query": "authentication bug",
    "assigned": "someone",
    "created_after": 7,
    "created_before": 1,
    "updated_after": 3,
    "updated_before": 2,
}

# Set by the project selection rather than by the shared filter code
PROJECT_KEYS = {"projects"}


def _maniphest(project_phids):
    """A Maniphest whose tag resolution yields the given project PHIDs."""
    maniphest = Maniphest()
    maniphest.phab = MagicMock()
    maniphest.url = "https://phabricator.example.com"
    maniphest.conf = {"PHAB_SPACE": "S1"}

    maniphest.phab.phid.lookup.return_value = {
        "S1": {
            "phid": "PHID-SPCE-1",
            "name": "Global",
            "fullName": "Global",
            "uri": "/S1",
        }
    }

    response = MagicMock()
    response.response = {"data": []}
    response.get.return_value = {"after": None}
    maniphest.phab.maniphest.search.return_value = response
    maniphest.phab.project.query.return_value = {"data": {}}

    maniphest._resolve_project_phids = MagicMock(return_value=project_phids)
    maniphest._resolve_user_phid = MagicMock(return_value=USER_PHID)
    maniphest._get_open_statuses = MagicMock(return_value=["open"])

    return maniphest


def _first_call_for(project_phids, tag, **extra):
    """Keyword arguments of the first maniphest.search call for one path."""
    maniphest = _maniphest(project_phids)
    maniphest.task_search(tag=tag, **FILTERS, **extra)

    assert maniphest.phab.maniphest.search.called, (
        f"no search issued for tag={tag!r}, project_phids={project_phids!r}"
    )
    return maniphest.phab.maniphest.search.call_args_list[0][1]


def _constraints_for(project_phids, tag):
    """Constraints of the first maniphest.search call for one path."""
    return _first_call_for(project_phids, tag)["constraints"]


def _shared_filters(constraints):
    return {k: v for k, v in constraints.items() if k not in PROJECT_KEYS}


@patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
class TestSearchFilterParity:
    """All three paths must apply the same secondary filters."""

    def test_single_project_matches_no_tag(self, mock_init):
        no_tag = _constraints_for([], tag=None)
        single = _constraints_for([PROJECT_A], tag="TeamA")

        assert _shared_filters(single) == _shared_filters(no_tag)

    def test_multiple_projects_match_single_project(self, mock_init):
        single = _constraints_for([PROJECT_A], tag="TeamA")
        multiple = _constraints_for([PROJECT_A, PROJECT_B], tag="TeamA")

        assert _shared_filters(multiple) == _shared_filters(single)

    def test_wildcard_tag_matches_no_tag(self, mock_init):
        no_tag = _constraints_for([], tag=None)
        wildcard = _constraints_for([], tag="*")

        assert _shared_filters(wildcard) == _shared_filters(no_tag)

    def test_every_filter_reaches_every_path(self, mock_init):
        """Guards against a path silently dropping a filter altogether."""
        expected = {
            "statuses",
            "query",
            "assigned",
            "createdStart",
            "createdEnd",
            "modifiedStart",
            "modifiedEnd",
        }

        for project_phids, tag in (
            ([], None),
            ([], "*"),
            ([PROJECT_A], "TeamA"),
            ([PROJECT_A, PROJECT_B], "TeamA"),
        ):
            constraints = _constraints_for(project_phids, tag)
            missing = expected - set(constraints)
            assert not missing, (
                f"tag={tag!r} projects={len(project_phids)} dropped {missing}"
            )

    def test_order_reaches_every_path(self, mock_init):
        """Ordering is a top-level argument, and no path may skip it."""
        for project_phids, tag in (
            ([], None),
            ([], "*"),
            ([PROJECT_A], "TeamA"),
            ([PROJECT_A, PROJECT_B], "TeamA"),
        ):
            default = _first_call_for(project_phids, tag)
            requested = _first_call_for(project_phids, tag, order="updated:asc")

            assert default.get("order") == "priority", (
                f"tag={tag!r} projects={len(project_phids)} lost the default order"
            )
            assert requested.get("order") == "outdated", (
                f"tag={tag!r} projects={len(project_phids)} lost --order"
            )

    def test_project_selection_still_differs_per_path(self, mock_init):
        """The parity checks above must not be vacuous."""
        assert "projects" not in _constraints_for([], tag=None)
        assert _constraints_for([PROJECT_A], tag="TeamA")["projects"] == [PROJECT_A]
        # Multiple projects are queried one at a time and merged
        assert _constraints_for([PROJECT_A, PROJECT_B], tag="TeamA")["projects"] == [
            PROJECT_A
        ]
