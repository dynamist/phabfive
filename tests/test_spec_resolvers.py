# -*- coding: utf-8 -*-

"""Projects, Spaces, colours and icons across the two validation layers (#494).

Four questions that look alike and are answered in four different places,
which is the whole point of this file:

===========  =======  =========================================================
``color:``   layer 1  Phorge fixes the ten colour keys in its own source, so a
                      colour needs no token and no socket. A `FieldKind.ENUM`.
``icon:``    layer 2  `projects.icons` is instance configuration that no
                      Conduit method reports, so an unrecognised icon is a
                      **warning** and never changes an exit status.
``projects:`` layer 2 One `project.query`, and ambiguity is a distinct verdict
                      from absence.
``space:``   layer 2  One enumeration of every visible Space, however many
                      items name one.
===========  =======  =========================================================

The app is mocked the way `tests/test_spec_validate_online.py` does it:
`Phabfive.__init__` is faked, `.phab` is a `MagicMock`, and a mock that fails
raises a phabfive exception type - never `phabricator.APIError`, which only
`phabfive/conduit.py` ever sees.
"""

# python std lib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# 3rd party imports
import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app as cli_app
from phabfive.cli import spec as cli_spec
from phabfive.constants import PROJECT_COLORS, PROJECT_ICONS
from phabfive.exceptions import (
    PhabfiveConnectionException,
    PhabfiveRemoteException,
)
from phabfive.maniphest.core import Maniphest
from phabfive.spec import Spec, parse_spec, validate_offline, validate_online
from phabfive.spec.online import (
    FIELD_SCOPED_KINDS,
    IconResolver,
    ProjectResolver,
    ReferenceGroup,
    ResolveResult,
    SpaceResolver,
    index_references,
)
from phabfive.spec.problems import Severity
from phabfive.spec.registry import FieldKind, field_by_name

REPOSITORY = Path(__file__).resolve().parent.parent

runner = CliRunner()


def scrubbed_environment():
    """As little environment as an interpreter needs to start.

    The same rules as `scrubbed_environment()` in
    `tests/test_spec_isolation.py`, which is where they are explained:
    `HOME` and the five Windows variables stay **defined** and point at a
    directory that does not exist, because `phabricator/__init__.py` reads
    `ProgramData` and `AppData` as it imports and an absent one is a
    `KeyError` before anything is validated. Restated rather than imported
    because pytest 9 imports test modules without putting `tests/` on
    `sys.path`.
    """
    keep = ("PATH", "SYSTEMROOT", "LD_LIBRARY_PATH", "VIRTUAL_ENV")
    environment = {name: os.environ[name] for name in keep if name in os.environ}

    nowhere = str(Path(os.sep, "nonexistent"))

    environment["HOME"] = nowhere
    environment["USERPROFILE"] = nowhere
    environment["APPDATA"] = nowhere
    environment["LOCALAPPDATA"] = nowhere
    environment["ProgramData"] = nowhere
    environment["ALLUSERSPROFILE"] = nowhere
    environment["PYTHONPATH"] = str(REPOSITORY)
    environment["TERM"] = "dumb"

    assert "PHAB_URL" not in environment
    assert "PHAB_TOKEN" not in environment

    return environment


# --------------------------------------------------------------------------
# A fake instance
# --------------------------------------------------------------------------


def a_project(phid, name, slugs=(), parent=None, identifier=1, icon="project"):
    """One project, in both the shapes the two endpoints answer with."""
    return {
        "phid": phid,
        "id": identifier,
        "name": name,
        "slugs": list(slugs),
        "parent": parent,
        "icon": icon,
    }


def _query_record(project):
    """`project.query`'s shape: name and slugs, keyed by PHID."""
    return {"name": project["name"], "slugs": project["slugs"]}


def _search_record(project):
    """`project.search`'s shape: everything under `fields`."""
    fields = {
        "name": project["name"],
        "slug": project["slugs"][0] if project["slugs"] else None,
        "icon": {"key": project["icon"]},
    }

    if project["parent"]:
        fields["parent"] = {"name": project["parent"]}

    return {"phid": project["phid"], "id": project["id"], "fields": fields}


def a_phab(projects=(), spaces=(), users=()):
    """A client that knows exactly what it is given, and counts its calls.

    `spaces` is a sequence of ``(monogram, name)``; `users` a sequence of
    ``(username, phid)``; `projects` the records :func:`a_project` builds.
    """
    phab = MagicMock()

    records = list(projects)

    def query(limit=100, offset=0):
        page = records[offset : offset + limit]
        return {"data": {one["phid"]: _query_record(one) for one in page}}

    def search(constraints=None, **kwargs):
        constraints = constraints or {}
        wanted_phids = set(constraints.get("phids", []))
        wanted_ids = {int(one) for one in constraints.get("ids", [])}
        matched = [
            one
            for one in records
            if (not wanted_phids and not wanted_ids)
            or one["phid"] in wanted_phids
            or one["id"] in wanted_ids
        ]
        return {"data": [_search_record(one) for one in matched]}

    by_monogram = {
        monogram: {"phid": f"PHID-SPCE-{monogram}", "name": name, "fullName": name}
        for monogram, name in spaces
    }

    def lookup(names):
        return {name: by_monogram[name] for name in names if name in by_monogram}

    def user_search(constraints=None, **kwargs):
        constraints = constraints or {}
        wanted = {one.casefold() for one in constraints.get("usernames", [])}
        return {
            "data": [
                {"phid": phid, "fields": {"username": username}}
                for username, phid in users
                if username.casefold() in wanted
            ]
        }

    phab.project.query.side_effect = query
    phab.project.search.side_effect = search
    phab.phid.lookup.side_effect = lookup
    phab.user.search.side_effect = user_search
    phab.user.whoami.return_value = {"phid": "PHID-USER-caller"}

    return phab


def an_app(phab):
    """An already-constructed app, which is all `validate_online` ever takes."""
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        app = Maniphest()

    app.phab = phab
    app.url = "http://phorge.localhost"
    app.conf = {"PHAB_URL": "http://phorge.localhost"}
    return app


def a_create_spec(*, tasks=(), projects=()):
    """A create spec holding the items given, with nothing else in it."""
    body = {"kind": "create"}

    if tasks:
        body["tasks"] = [{"title": f"Task {i}", **one} for i, one in enumerate(tasks)]

    if projects:
        body["projects"] = [
            {"name": f"Project {i}", **one} for i, one in enumerate(projects)
        ]

    return Spec.from_data(body, source="<test>")


def report(phab, spec, **kwargs):
    return validate_online(spec, an_app(phab), **kwargs)


def codes(problems):
    return [one.code for one in problems]


# --------------------------------------------------------------------------
# The rule: one report, however many kinds are wrong
# --------------------------------------------------------------------------


def test_a_bad_project_space_and_user_are_three_problems_from_one_run():
    """The issue's example: one problem reported, three missed. Now three."""
    spec = a_create_spec(
        tasks=[
            {
                "assignment": "@nosuchuser-zz",
                "projects": ["NoSuchProjectAtAll"],
                "space": "S9999",
            }
        ]
    )

    problems = report(a_phab(), spec)

    # The order is `references.REFERENCE_FIELDS["task"]`'s declaration order,
    # not the order the keys happen to be written in: one walk, one order,
    # and therefore a report a test can assert whole.
    assert codes(problems) == ["unknown-project", "unknown-user", "unknown-space"]
    assert [one.field for one in problems] == ["projects[0]", "assignment", "space"]
    assert {one.layer for one in problems} == {"online"}
    assert {one.severity for one in problems} == {Severity.ERROR}


def test_every_kind_costs_one_lookup_however_many_items_name_it():
    """Four tasks naming the same three things ask the same three questions."""
    phab = a_phab()
    spec = a_create_spec(
        tasks=[
            {"assignment": "nosuch", "projects": ["Nope"], "space": "S9"},
            {"assignment": "nosuch", "projects": ["Nope"], "space": "S9"},
            {"assignment": "nosuch", "projects": ["Nope"], "space": "S9"},
            {"assignment": "nosuch", "projects": ["Nope"], "space": "S9"},
        ]
    )

    problems = report(phab, spec)

    # Twelve references, twelve problems - each site is named - from three
    # questions asked once each.
    assert len(problems) == 12
    assert phab.user.search.call_count == 1
    assert phab.project.query.call_count == 1
    # Two, because `fetch_all_spaces` probes S1-S100 in chunks of 50: a
    # policy-filtered `phid.lookup` makes the visible monograms sparse, so
    # the whole range is asked about unconditionally.
    assert phab.phid.lookup.call_count == 2


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------


class TestProjects:
    PROJECTS = (
        a_project("PHID-PROJ-platform", "Platform", ["platform"], identifier=1),
        a_project("PHID-PROJ-infra", "Infrastructure", ["infra"], identifier=2),
    )

    def _phab(self):
        return a_phab(projects=self.PROJECTS)

    @pytest.mark.parametrize(
        "value",
        [
            "Platform",
            "platform",
            "PLATFORM",
            "#platform",
            "#infra",
            "PHID-PROJ-platform",
            "1",
        ],
    )
    def test_every_spelling_a_create_accepts_resolves(self, value):
        spec = a_create_spec(tasks=[{"projects": [value]}])

        assert report(self._phab(), spec) == []

    def test_a_project_that_does_not_exist_is_reported(self):
        spec = a_create_spec(tasks=[{"projects": ["Nope"]}])

        [problem] = report(self._phab(), spec)

        assert problem.code == "unknown-project"
        assert problem.field == "projects[0]"
        assert "Nope" in problem.reason

    def test_a_phid_is_answered_from_the_same_walk(self):
        """Every project came back, so a PHID costs no second request."""
        phab = self._phab()
        spec = a_create_spec(
            tasks=[{"projects": ["PHID-PROJ-infra", "PHID-PROJ-nope"]}]
        )

        [problem] = report(phab, spec)

        assert problem.value == "PHID-PROJ-nope"
        assert phab.project.query.call_count == 1
        assert phab.project.search.call_count == 0

    def test_numeric_ids_cost_one_request_between_them(self):
        """The one spelling the enumeration does not key, asked about once."""
        phab = self._phab()
        spec = a_create_spec(tasks=[{"projects": ["1", "2", "404"]}])

        [problem] = report(phab, spec)

        assert (problem.value, problem.code) == ("404", "unknown-project")
        assert phab.project.search.call_count == 1

    def test_a_pattern_that_matches_nothing_is_reported(self):
        """The resolver's own answer, for the filter keys that will use it.

        Reached directly rather than through a spec, because no create key
        accepts a glob: `projects:` is refused offline before the online
        pass ever sees it, which the class below is about. `tag:` is the
        key this branch is waiting for.
        """
        resolver = ProjectResolver()

        answers = resolver.resolve(an_app(self._phab()), ["plat*", "nope-*"])

        assert answers["plat*"].resolved
        assert answers["nope-*"].problem == "unknown-project"
        assert "pattern" in answers["nope-*"].reason

    def test_one_request_however_many_items_name_the_same_project(self):
        phab = self._phab()
        spec = a_create_spec(
            tasks=[{"projects": ["Platform", "#infra"]} for _ in range(5)]
        )

        assert report(phab, spec) == []
        assert phab.project.query.call_count == 1

    def test_a_connection_failure_is_not_an_unresolvable_reference(self):
        phab = self._phab()
        phab.project.query.side_effect = PhabfiveConnectionException("no route")
        spec = a_create_spec(tasks=[{"projects": ["Platform"]}])

        # The exact type, not the base class: `validate_online` promises a
        # PhabfiveConnectionException reaches its caller as itself, so that
        # "the request never landed" stays distinct from "Conduit refused
        # it" - `fetch_project_lookup_maps` used to flatten both.
        with pytest.raises(PhabfiveConnectionException):
            report(phab, spec)


class TestAWildcardIsNotACreateValue:
    """`maniphest create` refuses one, so validation has to as well.

    `resolve_project_phids_for_create` raises PhabfiveConfigException for any
    `projects:` value holding a "*", with no network at all. A spec that
    validated clean and was then refused on apply is exactly the promise the
    two layers exist to keep, so this is an **offline** problem.
    """

    def test_a_wildcard_in_projects_is_an_offline_problem(self):
        spec = a_create_spec(tasks=[{"projects": ["plat*"]}])

        [problem] = validate_offline(spec)

        assert problem.code == "unknown-value"
        assert problem.field == "projects[0]"
        assert problem.layer == "offline"
        assert "wildcard" in problem.reason

    def test_the_online_pass_is_never_asked_about_it(self):
        """The offline error stops the run, so no request is made."""
        phab = a_phab(projects=(a_project("PHID-PROJ-p", "Platform", ["plat"]),))
        spec = a_create_spec(tasks=[{"projects": ["plat*"]}])

        assert validate_offline(spec)
        assert phab.project.query.call_count == 0

    def test_the_apply_path_refuses_the_same_value(self):
        """The two halves of the promise, asserted together."""
        from phabfive.exceptions import PhabfiveConfigException
        from phabfive.maniphest.resolvers import resolve_project_phids_for_create

        phab = a_phab(projects=(a_project("PHID-PROJ-p", "Platform", ["plat"]),))

        with pytest.raises(PhabfiveConfigException) as error:
            resolve_project_phids_for_create(phab, ["plat*"])

        assert "Wildcards not allowed" in str(error.value)


class TestAmbiguousProjects:
    """Several projects can share a name; that is not the same as none."""

    PROJECTS = (
        a_project(
            "PHID-PROJ-s1dev", "Sprint 1", [], parent="Development", identifier=7
        ),
        a_project("PHID-PROJ-s1qa", "Sprint 1", [], parent="QA", identifier=8),
        a_project("PHID-PROJ-dev", "Development", ["dev"], identifier=1),
    )

    def test_an_ambiguous_name_is_ambiguous_not_absent(self):
        spec = a_create_spec(tasks=[{"projects": ["Sprint 1"]}])

        [problem] = report(a_phab(projects=self.PROJECTS), spec)

        assert problem.code == "ambiguous-project"
        assert problem.code != "unknown-project"

    def test_the_candidates_are_named(self):
        spec = a_create_spec(tasks=[{"projects": ["Sprint 1"]}])

        [problem] = report(a_phab(projects=self.PROJECTS), spec)

        assert "Sprint 1 (Development)" in problem.reason
        assert "Sprint 1 (QA)" in problem.reason
        assert "ID 7" in problem.reason

    def test_the_candidates_reach_the_result_as_well_as_the_sentence(self):
        resolver = ProjectResolver()
        app = an_app(a_phab(projects=self.PROJECTS))

        answers = resolver.resolve(app, ["Sprint 1"])

        assert answers["Sprint 1"].candidates == (
            "Sprint 1 (Development)",
            "Sprint 1 (QA)",
        )

    def test_describing_the_candidates_costs_one_request_for_all_of_them(self):
        phab = a_phab(projects=self.PROJECTS)
        spec = a_create_spec(tasks=[{"projects": ["Sprint 1", "sprint 1"]}])

        problems = report(phab, spec)

        assert codes(problems) == ["ambiguous-project", "ambiguous-project"]
        assert phab.project.search.call_count == 1

    def test_a_slug_still_resolves_although_the_name_is_ambiguous(self):
        """A hashtag is unique, so it wins over a shared primary name."""
        spec = a_create_spec(tasks=[{"projects": ["#dev"]}])

        assert report(a_phab(projects=self.PROJECTS), spec) == []


# --------------------------------------------------------------------------
# Spaces
# --------------------------------------------------------------------------


class TestSpaces:
    SPACES = (("S1", "Default"), ("S3", "Archive"))

    def _phab(self):
        return a_phab(spaces=self.SPACES)

    @pytest.mark.parametrize("value", ["S1", "S3", "Archive", "Arch*"])
    def test_a_monogram_a_name_and_a_pattern_all_resolve(self, value):
        spec = a_create_spec(tasks=[{"space": value}])

        assert report(self._phab(), spec) == []

    def test_a_space_that_does_not_exist_is_reported(self):
        spec = a_create_spec(tasks=[{"space": "S9999"}])

        [problem] = report(self._phab(), spec)

        assert problem.code == "unknown-space"
        assert problem.field == "space"
        assert "S9999" in problem.reason

    def test_a_pattern_matching_two_spaces_is_reported_not_guessed(self):
        """An object goes in exactly one Space, so two matches is a failure."""
        spec = a_create_spec(tasks=[{"space": "*"}])

        [problem] = report(self._phab(), spec)

        assert problem.code == "unknown-space"
        assert "S1" in problem.reason and "S3" in problem.reason

    def test_one_enumeration_however_many_items_name_a_space(self):
        phab = self._phab()
        spec = a_create_spec(
            tasks=[{"space": "S1"}, {"space": "S1"}, {"space": "Archive"}]
        )

        assert report(phab, spec) == []
        assert phab.phid.lookup.call_count == 2  # S1-S50, S51-S100

    def test_a_connection_failure_is_not_an_unresolvable_reference(self):
        phab = self._phab()
        phab.phid.lookup.side_effect = PhabfiveConnectionException("no route")
        spec = a_create_spec(tasks=[{"space": "S1"}])

        with pytest.raises(PhabfiveRemoteException):
            report(phab, spec)


# --------------------------------------------------------------------------
# Colours: layer 1, because Phorge fixes them in its source
# --------------------------------------------------------------------------


class TestColoursAreOffline:
    SPEC = "kind: create\nprojects:\n  - name: Platform\n    color: {colour}\n"

    def _check(self, colour):
        return validate_offline(
            parse_spec(self.SPEC.format(colour=colour), format="yaml")
        )

    @pytest.mark.parametrize("colour", PROJECT_COLORS)
    def test_every_colour_phorge_has_is_accepted(self, colour):
        assert self._check(colour) == []

    def test_a_colour_phorge_does_not_have_is_an_offline_error(self):
        [problem] = self._check("mauve")

        assert (problem.code, problem.layer) == ("unknown-value", "offline")
        assert problem.field == "color"
        assert "mauve" in problem.reason

    def test_the_declared_choices_are_the_colours_phorge_ships(self):
        """`registry.py` restates them because it may not import constants.

        `tests/test_spec_registry.py::test_the_registry_imports_only_the_standard_library`
        refuses `phabfive.constants` in the registry's import graph, and
        `choices` has to be a literal because FIELDS is built at import. This
        is what stops the restatement drifting.
        """
        colour = field_by_name("color", "project", "create")

        assert colour is not None
        assert list(colour.choices) == list(PROJECT_COLORS)
        assert colour.kind is FieldKind.ENUM

    def test_a_colour_is_never_asked_about_online(self):
        phab = a_phab()
        spec = a_create_spec(projects=[{"color": "blue"}])

        assert report(phab, spec) == []
        assert phab.project.query.call_count == 0
        assert phab.project.search.call_count == 0

    def test_the_project_keys_nothing_declares_yet_are_left_alone(self):
        """Declaring two keys of an object must not condemn the rest.

        ("project", "create") is deliberately not in `DECLARED_COMPLETE`.
        """
        problems = validate_offline(
            parse_spec(
                "kind: create\nprojects:\n"
                "  - name: Platform\n"
                "    description: A project\n"
                "    members: [alice]\n"
                "    color: blue\n",
                format="yaml",
            )
        )

        assert problems == []

    def test_a_bad_colour_fails_with_no_token_and_no_home(self):
        """Out of process: a colour is settled without a Phorge at all."""
        script = """
import json
from phabfive.spec import parse_spec, validate_offline

spec = parse_spec(
    "kind: create\\nprojects:\\n  - name: P\\n    color: mauve\\n", format="yaml"
)
print(json.dumps([one.code for one in validate_offline(spec)]))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=scrubbed_environment(),
            cwd=str(REPOSITORY),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == ["unknown-value"]


# --------------------------------------------------------------------------
# Icons: layer 2, and a warning rather than a verdict
# --------------------------------------------------------------------------


class TestIconsWarn:
    PROJECTS = (
        a_project("PHID-PROJ-a", "A", ["a"], identifier=1, icon="fa-custom"),
        a_project("PHID-PROJ-b", "B", ["b"], identifier=2, icon="project"),
    )

    def test_an_icon_no_project_uses_is_a_warning(self):
        spec = a_create_spec(projects=[{"icon": "fa-nope"}])

        [problem] = report(a_phab(projects=self.PROJECTS), spec)

        assert problem.code == "unknown-icon"
        assert problem.severity == Severity.WARNING
        assert problem.layer == "online"
        assert "misspelled" in problem.reason

    def test_an_icon_a_project_carries_resolves(self):
        spec = a_create_spec(projects=[{"icon": "fa-custom"}])

        assert report(a_phab(projects=self.PROJECTS), spec) == []

    @pytest.mark.parametrize("icon", [PROJECT_ICONS[0], PROJECT_ICONS[-1]])
    def test_a_stock_icon_asks_the_instance_nothing(self, icon):
        phab = a_phab(projects=self.PROJECTS)
        spec = a_create_spec(projects=[{"icon": icon}])

        assert report(phab, spec) == []
        assert phab.project.search.call_count == 0

    def test_the_milestone_icon_is_not_a_typo(self):
        """Phorge gives it to every milestone, so no project may carry it."""
        spec = a_create_spec(projects=[{"icon": "milestone"}])

        assert report(a_phab(projects=self.PROJECTS), spec) == []

    def test_one_request_however_many_items_name_the_icon(self):
        phab = a_phab(projects=self.PROJECTS)
        spec = a_create_spec(projects=[{"icon": "fa-nope"} for _ in range(4)])

        problems = report(phab, spec)

        assert codes(problems) == ["unknown-icon"] * 4
        assert phab.project.search.call_count == 1

    def test_a_connection_failure_is_not_an_unknown_icon(self):
        phab = a_phab(projects=self.PROJECTS)
        phab.project.search.side_effect = PhabfiveConnectionException("no route")
        spec = a_create_spec(projects=[{"icon": "fa-nope"}])

        with pytest.raises(PhabfiveConnectionException):
            report(phab, spec)


class TestAWarningCostsNoExitStatus:
    """End to end, because the exit status is the whole claim."""

    SPEC = "kind: create\nprojects:\n  - name: Platform\n    icon: fa-nope\n"

    def _run(self, tmp_path, text):
        path = tmp_path / "spec.yaml"
        path.write_text(text, encoding="utf-8")

        app = an_app(a_phab(projects=TestIconsWarn.PROJECTS))

        with patch.object(cli_spec, "_online_app", return_value=app):
            return runner.invoke(
                cli_app, ["--format=rich", "spec", "validate", str(path)]
            )

    def test_an_unknown_icon_is_reported_and_exits_zero(self, tmp_path):
        result = self._run(tmp_path, self.SPEC)

        assert result.exit_code == 0
        assert "unknown-icon" in result.stdout

    def test_a_bad_colour_stops_the_run_before_the_icon_is_asked_about(self, tmp_path):
        """An offline error decides the status, and layer 2 never runs.

        So the warning is not merely outvoted here - it is never produced,
        which is `spec validate`'s documented short circuit rather than
        anything about severities. The companion below is where a warning
        and an error really do land in one report.
        """
        result = self._run(tmp_path, self.SPEC + "    color: mauve\n")

        assert result.exit_code == 1
        assert "unknown-value" in result.stdout
        assert "unknown-icon" not in result.stdout
        assert "1 error, 0 warnings" in result.stdout

    def test_a_warning_beside_an_online_error_still_exits_two(self, tmp_path):
        """Both come out of layer 2, so both are reported and the error wins."""
        result = self._run(
            tmp_path,
            self.SPEC + "tasks:\n  - title: T\n    projects: [NoSuchProject]\n",
        )

        assert result.exit_code == 2
        assert "unknown-icon" in result.stdout
        assert "unknown-project" in result.stdout
        assert "1 error, 1 warning" in result.stdout


# --------------------------------------------------------------------------
# The seam: one kind, several lookups
# --------------------------------------------------------------------------


class FakeResolver:
    """A resolver that answers from a dict, and counts its calls."""

    def __init__(self, kind, answers, fields=frozenset()):
        self.kind = kind
        self.fields = fields
        self._answers = answers
        self.calls = []

    def resolve(self, app, values):
        self.calls.append(tuple(values))
        return {
            value: self._answers[value] for value in values if value in self._answers
        }


class TestGroups:
    def test_an_instance_enum_groups_by_field(self):
        """Status, priority and icon are one kind and three questions."""
        assert FieldKind.INSTANCE_ENUM in FIELD_SCOPED_KINDS

        spec = a_create_spec(projects=[{"icon": "fa-nope"}])

        assert index_references(spec).groups() == (
            ReferenceGroup(FieldKind.INSTANCE_ENUM, "icon"),
        )

    def test_every_other_kind_groups_by_kind_alone(self):
        """Two keys naming projects stay one group, and one request."""
        spec = a_create_spec(tasks=[{"projects": ["A"], "space": "S1"}])

        assert index_references(spec).groups() == (
            ReferenceGroup(FieldKind.PROJECT),
            ReferenceGroup(FieldKind.SPACE),
        )

    def test_a_field_scoped_resolver_answers_only_its_own_field(self):
        """A resolver registered for `icon:` is not asked about a status."""
        status = FakeResolver(
            FieldKind.INSTANCE_ENUM,
            {"fa-nope": ResolveResult(value="fa-nope", problem="unknown-status")},
            fields=frozenset({"status"}),
        )
        spec = a_create_spec(projects=[{"icon": "fa-nope"}])

        problems = report(
            a_phab(projects=TestIconsWarn.PROJECTS),
            spec,
            resolvers=[status, IconResolver()],
        )

        assert status.calls == []
        assert codes(problems) == ["unknown-icon"]

    def test_a_kind_wide_resolver_answers_a_field_no_one_claimed(self):
        anything = FakeResolver(
            FieldKind.INSTANCE_ENUM,
            {"fa-nope": ResolveResult(value="fa-nope", problem="unknown-value")},
        )
        spec = a_create_spec(projects=[{"icon": "fa-nope"}])

        problems = report(a_phab(), spec, resolvers=[anything])

        assert anything.calls == [("fa-nope",)]
        assert codes(problems) == ["unknown-value"]

    def test_a_caller_beats_the_default_for_one_field(self):
        mine = FakeResolver(
            FieldKind.INSTANCE_ENUM,
            {"fa-nope": ResolveResult(value="fa-nope")},
            fields=frozenset({"icon"}),
        )
        phab = a_phab(projects=TestIconsWarn.PROJECTS)
        spec = a_create_spec(projects=[{"icon": "fa-nope"}])

        assert report(phab, spec, resolvers=[mine, IconResolver()]) == []
        assert phab.project.search.call_count == 0

    def test_the_kind_wide_views_still_answer_across_groups(self):
        spec = a_create_spec(
            tasks=[{"projects": ["A"]}], projects=[{"icon": "fa-nope"}]
        )
        index = index_references(spec)

        assert index.kinds() == (FieldKind.PROJECT, FieldKind.INSTANCE_ENUM)
        assert index.values(FieldKind.INSTANCE_ENUM) == ("fa-nope",)
        assert len(index.sites(FieldKind.PROJECT, "A")) == 1


class TestTheResolversAreLibraryCode:
    """They may be imported without importing an app."""

    def test_the_lookups_are_imported_inside_resolve(self):
        """`tests/test_spec_isolation.py` proves the module; this says why.

        Every resolver defers its `phabfive.maniphest` and `phabfive.project`
        import into `resolve`, because importing either reaches
        `phabfive.core`, `phabricator` and `requests` - and the offline path
        must not.

        Asserted against `phabfive.spec.online`'s **module-level** imports,
        parsed rather than grepped: an earlier draft looked for "\nfrom
        phabfive.maniphest" in the class source, which an indented import
        can never contain - so hoisting the import to module level, the one
        regression it was written for, made it pass more easily.
        """
        import ast
        import inspect

        import phabfive.spec.online as module

        tree = ast.parse(inspect.getsource(module))

        top_level = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        top_level.update(
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        # The TYPE_CHECKING block is a module-level `if`, and its imports do
        # not run - so they are read out of `tree.body` above and are not here
        forbidden = {"phabfive.maniphest", "phabfive.project", "phabfive.cli"}
        assert not {
            name for name in top_level if any(name.startswith(one) for one in forbidden)
        }

        # And each resolver really does import one of them, inside a method
        for resolver in (ProjectResolver, SpaceResolver, IconResolver):
            deferred = [
                node
                for node in ast.walk(ast.parse(inspect.getsource(resolver)))
                if isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("phabfive.")
            ]

            assert deferred, resolver.__name__
