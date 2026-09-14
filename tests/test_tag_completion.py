# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

# phabfive imports
from phabfive.cli import completers
from phabfive.cli.completers import TAG_COMPLETION_LIMIT, complete_tag


def _project(id_, name, parent=None):
    return {
        "id": id_,
        "phid": f"PHID-PROJ-{id_}",
        "fields": {
            "name": name,
            "parent": {"id": 0, "phid": "PHID-PROJ-p", "name": parent}
            if parent
            else None,
        },
    }


def _complete_with(phab, incomplete):
    """Run complete_tag against a fake API client."""
    with patch.object(
        completers,
        "_get_values_with_api_fallback",
        side_effect=lambda fetch, default: fetch(phab),
    ):
        return complete_tag(incomplete)


def _phab(projects, page_size=100):
    """Fake project.search: word-prefix "name" constraint, paged by cursor."""
    phab = MagicMock()

    def search(constraints, limit=100, after=None):
        matches = projects
        if "name" in constraints:
            text = constraints["name"].lower()
            matches = [
                p
                for p in projects
                if any(w.startswith(text) for w in p["fields"]["name"].lower().split())
                or p["fields"]["name"].lower().startswith(text)
            ]
        start = int(after or 0)
        size = min(limit, page_size)
        page = matches[start : start + size]
        next_after = str(start + size) if start + size < len(matches) else None
        return {"data": page, "cursor": {"after": next_after}}

    phab.project.search.side_effect = search
    return phab


PROJECTS = [
    _project(1, "Kanban Board"),
    _project(2, "GUNNAR-Core"),
    _project(3, "QA"),
    _project(8, "Sprint 1", parent="Development"),
    _project(9, "Sprint 1", parent="QA"),
    _project(10, "Release Candidate", parent="QA"),
]


class TestServerSideLookup:
    def test_passes_typed_text_as_name_constraint(self):
        phab = _phab(PROJECTS)
        _complete_with(phab, "Kan")
        assert phab.project.search.call_args.kwargs["constraints"] == {"name": "Kan"}

    def test_no_constraint_without_typed_text(self):
        phab = _phab(PROJECTS)
        _complete_with(phab, "")
        assert phab.project.search.call_args.kwargs["constraints"] == {}

    def test_follows_cursor_beyond_first_page(self):
        """#267: projects after the first 100 are found."""
        projects = [_project(i, f"Team {i:03d}") for i in range(250)]
        phab = _phab(projects, page_size=100)

        result = _complete_with(phab, "Team")

        assert len(result) == 250
        assert "Team 249" in result
        assert phab.project.search.call_count == 3

    def test_stops_at_limit(self):
        projects = [
            _project(i, f"Team {i:04d}") for i in range(TAG_COMPLETION_LIMIT + 300)
        ]
        phab = _phab(projects, page_size=100)

        _complete_with(phab, "Team")

        fetched = TAG_COMPLETION_LIMIT // 100
        assert phab.project.search.call_count == fetched

    def test_api_failure_offers_nothing(self):
        with patch.object(completers, "_get_values_with_api_fallback", return_value=[]):
            assert complete_tag("Kan") == []


class TestMatching:
    def test_only_names_starting_with_typed_text(self):
        """The name constraint also matches later words, e.g. "Core"."""
        assert _complete_with(_phab(PROJECTS), "Core") == []

    @pytest.mark.parametrize(
        "incomplete, expected",
        [
            ("Kan", "Kanban Board"),
            ("kan", "kanban board"),
            ("KAN", "KANBAN BOARD"),
            ("kAn", "kAnban Board"),
            ("GUN", "GUNNAR-Core"),
            ("gun", "gunnar-core"),
        ],
    )
    def test_follows_typed_case(self, incomplete, expected):
        """Typer drops completions that don't start with the typed text."""
        assert _complete_with(_phab(PROJECTS), incomplete) == [expected]


class TestDuplicateNames:
    def test_shared_name_is_offered_once_with_ids(self):
        """#277: two "Sprint 1" milestones become one entry with their IDs."""
        assert _complete_with(_phab(PROJECTS), "Spr") == [
            ("Sprint 1", "ambiguous, use the ID: 8 (Development), 9 (QA)")
        ]

    def test_milestone_is_described_with_parent(self):
        assert _complete_with(_phab(PROJECTS), "Rel") == [
            ("Release Candidate", "in QA")
        ]

    def test_top_level_project_has_no_description(self):
        assert _complete_with(_phab(PROJECTS), "QA") == ["QA"]
