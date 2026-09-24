# -*- coding: utf-8 -*-

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest

# phabfive imports
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.maniphest import Maniphest
from phabfive.maniphest.resolvers import (
    ambiguous_project_message,
    fetch_project_lookup_maps,
    resolve_project_phids,
    resolve_project_phids_for_create,
)


def _project(id_, name, parent=None, slug=None):
    return {
        "id": id_,
        "phid": f"PHID-PROJ-{id_}",
        "fields": {
            "name": name,
            "slug": slug,
            "parent": {"id": 0, "phid": "PHID-PROJ-p", "name": parent}
            if parent
            else None,
        },
    }


# Two milestones sharing a name, a project with a hashtag that equals another
# project's name, and an ordinary project
PROJECTS = [
    _project(4, "Development", slug="development"),
    _project(5, "QA", slug="qa"),
    _project(8, "Sprint 1", parent="Development"),
    _project(9, "Sprint 1", parent="QA"),
    _project(10, "Release", slug="release"),
    _project(11, "Hotfixes", slug="deploy"),
    _project(12, "Deploy"),
]

AMBIGUOUS = (
    "Project name 'Sprint 1' is ambiguous, it matches: "
    "Sprint 1 (Development), ID 8; Sprint 1 (QA), ID 9. "
    "Use the project ID or PHID instead."
)


def _mock_phab():
    phab = MagicMock()

    def search(constraints):
        if "slugs" in constraints:
            slugs = [s.lower() for s in constraints["slugs"]]
            return {"data": [p for p in PROJECTS if p["fields"]["slug"] in slugs]}
        if "query" in constraints:
            words = constraints["query"].lower().split()
            return {
                "data": [
                    p
                    for p in PROJECTS
                    if all(w in p["fields"]["name"].lower() for w in words)
                ]
            }
        if "phids" in constraints:
            return {"data": [p for p in PROJECTS if p["phid"] in constraints["phids"]]}
        if "ids" in constraints:
            return {"data": [p for p in PROJECTS if p["id"] in constraints["ids"]]}
        return {"data": PROJECTS}

    phab.project.search.side_effect = search
    phab.project.query.return_value = {
        "data": {
            p["phid"]: {
                "id": p["id"],
                "name": p["fields"]["name"],
                "slugs": [p["fields"]["slug"]] if p["fields"]["slug"] else [],
            }
            for p in PROJECTS
        }
    }
    return phab


class TestAmbiguousProjectMessage:
    def test_lists_parent_and_id(self):
        assert ambiguous_project_message("Sprint 1", PROJECTS[2:4]) == AMBIGUOUS

    def test_falls_back_to_phids(self):
        message = ambiguous_project_message("Sprint 1", ["PHID-PROJ-8", "PHID-PROJ-9"])
        assert "PHID-PROJ-8; PHID-PROJ-9" in message


class TestSearchResolution:
    def test_ambiguous_name_is_rejected(self, caplog):
        assert resolve_project_phids(_mock_phab(), "sprint 1") == []
        assert AMBIGUOUS.replace("'Sprint 1'", "'sprint 1'") in caplog.text

    def test_ambiguous_name_is_rejected_in_fallback(self, caplog):
        """Also when the name query fails and all projects are fetched."""
        phab = _mock_phab()
        search = phab.project.search.side_effect

        def search_without_query(constraints):
            if "query" in constraints:
                raise Exception("query unavailable")
            return search(constraints)

        phab.project.search.side_effect = search_without_query

        assert resolve_project_phids(phab, "Sprint 1") == []
        assert AMBIGUOUS in caplog.text

    def test_unique_name_resolves(self):
        assert resolve_project_phids(_mock_phab(), "Release") == ["PHID-PROJ-10"]

    def test_id_reaches_each_milestone(self):
        phab = _mock_phab()
        assert resolve_project_phids(phab, "8") == ["PHID-PROJ-8"]
        assert resolve_project_phids(phab, "9") == ["PHID-PROJ-9"]

    def test_hashtag_wins_over_name(self):
        """'deploy' is the hashtag of Hotfixes and the name of another project."""
        assert resolve_project_phids(_mock_phab(), "deploy") == ["PHID-PROJ-11"]

    def test_wildcard_still_matches_every_duplicate(self):
        result = resolve_project_phids(_mock_phab(), "Sprint*")
        assert sorted(result) == ["PHID-PROJ-8", "PHID-PROJ-9"]


class TestCreateResolution:
    def test_lookup_maps_report_ambiguous_names(self):
        name_to_phid, _, ambiguous = fetch_project_lookup_maps(_mock_phab())

        assert "sprint 1" not in name_to_phid
        assert ambiguous == {"sprint 1": ["PHID-PROJ-8", "PHID-PROJ-9"]}

    def test_lookup_maps_prefer_hashtags(self):
        name_to_phid, _, _ = fetch_project_lookup_maps(_mock_phab())

        assert name_to_phid["deploy"] == "PHID-PROJ-11"
        assert name_to_phid["release"] == "PHID-PROJ-10"

    def test_ambiguous_name_is_rejected(self):
        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_project_phids_for_create(_mock_phab(), ["Sprint 1"])
        assert str(excinfo.value) == AMBIGUOUS

    def test_id_reaches_each_milestone(self):
        result = resolve_project_phids_for_create(_mock_phab(), ["8", "9"])
        assert result["phids"] == ["PHID-PROJ-8", "PHID-PROJ-9"]

    def test_hashtag_wins_over_name(self):
        result = resolve_project_phids_for_create(_mock_phab(), ["deploy"])
        assert result["phids"] == ["PHID-PROJ-11"]

    def test_create_and_search_agree(self):
        """The same unique name resolves to the same project in both paths."""
        phab = _mock_phab()
        for name in ("Release", "deploy", "QA"):
            created = resolve_project_phids_for_create(phab, [name])["phids"]
            assert created == resolve_project_phids(phab, name)


class TestYamlCreateResolution:
    """A template names a project; ambiguity is refused before anything is made.

    The template path runs on the spec engine since #480, so the refusal is
    a `PhabfiveDataException` naming every problem in the document rather
    than a `PhabfiveConfigException` carrying the first one's sentence
    alone. The sentence itself is unchanged - it is still
    `ambiguous_project_message` - and it now says which task and which key
    wrote the name.
    """

    @patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None)
    def test_ambiguous_name_is_rejected(self, mock_init, tmp_path):
        config = tmp_path / "tasks.yaml"
        config.write_text(
            "variables: {}\n"
            "tasks:\n"
            "  - title: Plan the sprint\n"
            "    projects:\n"
            "      - Sprint 1\n"
        )
        maniphest = Maniphest()
        maniphest.phab = _mock_phab()
        maniphest.phab.user.search.return_value = {"data": []}

        with pytest.raises(PhabfiveDataException) as excinfo:
            maniphest.create_tasks_from_yaml(str(config), dry_run=True)

        assert str(excinfo.value) == (
            f"1 problem(s) in this spec:\n  - tasks[0].projects[0]: {AMBIGUOUS}"
        )
        maniphest.phab.maniphest.edit.assert_not_called()


class TestCreateCommand:
    @pytest.mark.parametrize("spec_file", [False, True])
    @patch("phabfive.cli.maniphest._get_maniphest_app")
    def test_ambiguous_tag_is_a_clean_error(self, mock_get_app, spec_file, tmp_path):
        """Both ways in, because both used to answer with a traceback.

        `--with` is `phabfive apply -f` under its old name since the three
        `create --with` commands were unified (`phabfive.cli.create_spec`),
        so the ambiguity is raised where the planner resolves the name
        rather than inside the template recursion - and it still has to
        reach the terminal as one sentence and exit 1.
        """
        import phabfive.create
        from typer.testing import CliRunner

        from phabfive.cli.maniphest import maniphest_app

        maniphest = MagicMock()
        maniphest.create_task.side_effect = PhabfiveConfigException(AMBIGUOUS)
        mock_get_app.return_value = maniphest

        if spec_file:
            spec = tmp_path / "tasks.yaml"
            spec.write_text(
                "spec: phorge/v1alpha1\nkind: create\n"
                "tasks:\n  - title: Plan the sprint\n    projects: [Sprint 1]\n"
            )
            args = ["create", "--with", str(spec)]
        else:
            args = [
                "create",
                "Plan the sprint",
                "--tag",
                "Sprint 1",
                "--description",
                "",
            ]

        with patch.object(
            phabfive.create,
            "plan_spec",
            side_effect=PhabfiveConfigException(AMBIGUOUS),
        ):
            result = CliRunner().invoke(maniphest_app, args)

        assert result.exit_code == 1
        assert f"Error: {AMBIGUOUS}" in result.output
        assert not isinstance(result.exception, PhabfiveConfigException)
