# -*- coding: utf-8 -*-

"""The project app: resolving, reading and writing Phorge projects."""

# python std lib
import copy
import json

# 3rd party imports
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

# phabfive imports
from phabfive.cli import app
from phabfive.cli.output import is_machine_format
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.project import Project
from phabfive.project.formatters import build_project_display_data
from phabfive.project.resolvers import resolve_project, resolve_user_phids

runner = CliRunner()

URL = "http://phorge.localhost"


def _project(
    pid,
    name,
    slug=None,
    parent=None,
    milestone=None,
    status="active",
    icon="project",
    color="blue",
    policy=None,
    members=(),
    space="PHID-SPCE-default",
):
    """A project.search result item, shaped the way the API returns one."""
    return {
        "id": pid,
        "phid": f"PHID-PROJ-{pid}",
        "fields": {
            "name": name,
            "slug": slug,
            "milestone": milestone,
            "depth": 1 if parent else 0,
            "parent": (
                {
                    "id": parent["id"],
                    "phid": parent["phid"],
                    "name": parent["fields"]["name"],
                }
                if parent
                else None
            ),
            "icon": {"key": icon, "name": icon.title(), "icon": f"fa-{icon}"},
            "color": {"key": color, "name": color.title()},
            "status": status,
            "spacePHID": space,
            "dateCreated": 1789875812,
            "dateModified": 1789875812,
            "policy": policy or {"view": "users", "edit": "users", "join": "users"},
            "description": "",
        },
        "attachments": {
            "members": {"members": [{"phid": phid} for phid in members]},
        },
    }


def _user(username, real_name="", roles=("verified", "approved", "activated")):
    return {
        "id": abs(hash(username)) % 1000,
        "phid": f"PHID-USER-{username}",
        "fields": {"username": username, "realName": real_name, "roles": list(roles)},
    }


DEVELOPMENT = _project(
    12, "Development", slug="development", members=["PHID-USER-admin"]
)
QA = _project(14, "QA", slug="qa")
SPRINT_DEV = _project(13, "Sprint 1", parent=DEVELOPMENT, milestone=1, icon="milestone")
SPRINT_QA = _project(15, "Sprint 1", parent=QA, milestone=1, icon="milestone")
HUMANS = _project(
    20,
    "Humans",
    slug="humans",
    members=["PHID-USER-admin", "PHID-USER-deploybot"],
    policy={"view": "users", "edit": "PHID-PROJ-20", "join": "admin"},
)
OLD = _project(30, "Old Project", slug="old_project", status="archived")

ADMIN = _user("admin", "Administrator", roles=("admin", "verified"))
DEPLOYBOT = _user("deploybot", "Deploy Bot", roles=("bot", "verified"))


def _stored(project, what):
    """The icon or colour stored on a project, which a search matches."""
    return project.get("_stored", {}).get(what) or project["fields"][what]["key"]


class FakePhab:
    """Just enough of project.search and user.search to answer like Phorge.

    project.search pages at `page_size`, which is how the audit test sees
    the cursor followed.
    """

    def __init__(self, projects, users=(), page_size=100):
        # Copied, so a test that edits one cannot leak into the next
        self.projects = copy.deepcopy(list(projects))
        self.users = {user["phid"]: user for user in users}
        self.page_size = page_size
        self.project = MagicMock()
        self.project.search.side_effect = self._project_search
        self.project.edit.return_value = {"object": {"id": 99, "phid": "PHID-PROJ-new"}}
        self.user = MagicMock()
        self.user.search.side_effect = self._user_search
        self.user.whoami.return_value = {"phid": "PHID-USER-admin", "userName": "admin"}
        self.phid = MagicMock()
        self.phid.query.side_effect = self._phid_query
        self.slugs = {}
        self.project.query.side_effect = self._project_query

    def _project_query(self, phids=None, **_):
        """The legacy method, the only one that reports every hashtag."""
        data = {}
        for project in self.projects:
            if project["phid"] in (phids or []):
                primary = project["fields"]["slug"]
                data[project["phid"]] = {
                    "slugs": ([primary] if primary else [])
                    + self.slugs.get(project["phid"], [])
                }
        return {"data": data}

    def _project_search(
        self, constraints=None, attachments=None, limit=100, after=None, **_
    ):
        constraints = constraints or {}
        found = self.projects

        if "slugs" in constraints:
            slugs = {slug.lower().replace(" ", "_") for slug in constraints["slugs"]}
            found = [p for p in found if p["fields"]["slug"] in slugs]
        if "ids" in constraints:
            found = [p for p in found if p["id"] in constraints["ids"]]
        if "phids" in constraints:
            found = [p for p in found if p["phid"] in constraints["phids"]]
        if "query" in constraints:
            text = constraints["query"].lower()
            found = [p for p in found if text in p["fields"]["name"].lower()]
        if "name" in constraints:
            word = constraints["name"].lower()
            found = [p for p in found if word in p["fields"]["name"].lower()]
        if "isMilestone" in constraints:
            found = [
                p
                for p in found
                if (p["fields"]["milestone"] is not None) == constraints["isMilestone"]
            ]
        # Like Phorge, icons and colors match the values stored on a project,
        # which for a milestone are not the ones its fields report
        if "icons" in constraints:
            found = [p for p in found if _stored(p, "icon") in constraints["icons"]]
        if "colors" in constraints:
            found = [p for p in found if _stored(p, "color") in constraints["colors"]]

        status = constraints.get("status", "active")
        if status != "all":
            found = [p for p in found if p["fields"]["status"] == status]

        start = int(after or 0)
        size = min(limit, self.page_size)
        page = found[start : start + size]
        more = start + size < len(found)

        return {
            "data": page,
            "cursor": {"after": str(start + size) if more else None},
        }

    def _user_search(self, constraints=None, **_):
        constraints = constraints or {}
        users = list(self.users.values())

        if "phids" in constraints:
            users = [u for u in users if u["phid"] in constraints["phids"]]
        if "usernames" in constraints:
            names = {name.lower() for name in constraints["usernames"]}
            users = [u for u in users if u["fields"]["username"].lower() in names]

        return {"data": users, "cursor": {"after": None}}

    def _phid_query(self, phids):
        names = {}
        for phid in phids:
            if phid.startswith("PHID-PROJ-"):
                project = next((p for p in self.projects if p["phid"] == phid), None)
                if project and project["fields"]["slug"]:
                    names[phid] = {
                        "type": "PROJ",
                        "name": project["fields"]["name"],
                        "uri": f"{URL}/tag/{project['fields']['slug']}/",
                    }
            elif phid.startswith("PHID-SPCE-"):
                names[phid] = {
                    "type": "SPCE",
                    "name": "Default",
                    "fullName": "S1 Default",
                }
        return names


def _app(phab):
    """A Project wired to a fake API, without the two round trips __init__ makes."""
    with patch("phabfive.project.core.Phabfive.__init__", return_value=None):
        project = Project()

    project.phab = phab
    project.url = URL
    project.conf = {"PHAB_SPACE": "S1"}
    project.format_link = lambda url, text: url

    return project


ALL = [DEVELOPMENT, SPRINT_DEV, QA, SPRINT_QA, HUMANS, OLD]


@pytest.fixture(autouse=True)
def spaces():
    """Resolve a Space pattern to a PHID named after it, without the API."""
    with patch(
        "phabfive.project.core.resolve_space_phids",
        side_effect=lambda phab, space: [f"PHID-SPCE-{space}"],
    ) as resolve:
        yield resolve


@pytest.fixture
def phab():
    return FakePhab(ALL, users=[ADMIN, DEPLOYBOT])


@pytest.fixture
def restore_output_format():
    original = Phabfive._output_format
    try:
        yield
    finally:
        Phabfive._output_format = original


def _invoke(phab, args):
    """Run the CLI against the fake API."""
    with patch("phabfive.cli.project._get_project_app", return_value=_app(phab)):
        return runner.invoke(app, args)


class TestResolveProject:
    @pytest.mark.parametrize(
        "ident", ["#development", "development", "12", "PHID-PROJ-12"]
    )
    def test_every_spelling_names_the_same_project(self, phab, ident):
        assert resolve_project(phab, ident)["id"] == 12

    def test_an_exact_name(self, phab):
        assert resolve_project(phab, "Humans")["id"] == 20

    def test_a_name_is_matched_whole_not_by_word(self, phab):
        """The name constraint matches on words; "Old" is not "Old Project"."""
        phab.projects[-1]["fields"]["status"] = "active"

        with pytest.raises(PhabfiveDataException, match="does not exist"):
            resolve_project(phab, "Old")

    def test_a_shared_name_is_refused_with_the_id_of_each(self, phab):
        """Every team's milestones are called something like "Sprint 1"."""
        with pytest.raises(PhabfiveDataException) as error:
            resolve_project(phab, "Sprint 1")

        message = str(error.value)
        assert "ambiguous" in message
        assert "Sprint 1 (Development), ID 13" in message
        assert "Sprint 1 (QA), ID 15" in message

    def test_a_milestone_is_reached_by_its_id(self, phab):
        assert resolve_project(phab, "15")["fields"]["parent"]["name"] == "QA"

    @pytest.mark.parametrize("ident", ["#nope", "nope", "999", "PHID-PROJ-nope"])
    def test_nothing_found_says_so(self, phab, ident):
        with pytest.raises(PhabfiveDataException, match="does not exist"):
            resolve_project(phab, ident)

    def test_nothing_given_is_a_usage_error(self, phab):
        with pytest.raises(PhabfiveConfigException):
            resolve_project(phab, "  ")


class TestResolveUsers:
    def test_with_or_without_the_at(self, phab):
        assert resolve_user_phids(phab, ["@admin", "deploybot"]) == {
            "@admin": ("PHID-USER-admin", "admin"),
            "deploybot": ("PHID-USER-deploybot", "deploybot"),
        }

    def test_me_is_whoever_runs_the_command(self, phab):
        assert resolve_user_phids(phab, ["@me"]) == {
            "@me": ("PHID-USER-admin", "admin")
        }

    def test_one_lookup_for_every_name(self, phab):
        resolve_user_phids(phab, ["@admin", "@deploybot"])

        assert phab.user.search.call_count == 1

    def test_every_unknown_user_is_named(self, phab):
        with pytest.raises(PhabfiveDataException) as error:
            resolve_user_phids(phab, ["@admin", "@nobody", "@ghost"])

        assert "'@nobody'" in str(error.value)
        assert "'@ghost'" in str(error.value)


class TestRecord:
    def test_a_root_project(self):
        (record,) = build_project_display_data(
            URL, lambda url, text: url, [DEVELOPMENT]
        )

        assert record["_url"] == f"{URL}/project/view/12/"
        assert record["Project"] == {
            "Name": "Development",
            "Hashtag": "#development",
            "Status": "active",
            "Icon": "project",
            "Color": "blue",
            "Parent": None,
            "Milestone": None,
            "Description": "",
        }

    def test_a_milestone_carries_the_same_keys(self):
        """A table has the same columns on every row, a milestone included."""
        (record,) = build_project_display_data(URL, lambda url, text: url, [SPRINT_DEV])

        assert record["Project"]["Hashtag"] is None
        assert record["Project"]["Parent"] == "Development"
        assert record["Project"]["Milestone"] == 1

    def test_sections_appear_only_when_asked_for(self):
        (record,) = build_project_display_data(
            URL, lambda url, text: url, [DEVELOPMENT]
        )

        assert "Policy" not in record
        assert "Members" not in record
        assert "Metadata" not in record


class TestShow:
    def test_members_carry_their_roles(self, phab):
        """What tells a bot account from a person, passed through unchanged."""
        result = _app(phab).show(["#humans"], show_members=True)

        (record,) = result["projects"]
        assert record["Members"] == [
            {
                "Username": "admin",
                "Name": "Administrator",
                "Roles": ["admin", "verified"],
            },
            {
                "Username": "deploybot",
                "Name": "Deploy Bot",
                "Roles": ["bot", "verified"],
            },
        ]

    def test_members_are_not_fetched_unless_asked_for(self, phab):
        _app(phab).show(["#humans"])

        phab.user.search.assert_not_called()

    def test_a_policy_is_named_in_the_spelling_the_options_take(self, phab):
        result = _app(phab).show(["#humans"], show_policy=True)

        assert result["projects"][0]["Policy"] == {
            "Visible To": "All Users",
            "Editable By": "#humans",
            "Joinable By": "Administrators",
        }

    def test_the_same_project_named_twice_is_shown_once(self, phab):
        result = _app(phab).show(["#development", "12"])

        assert len(result["projects"]) == 1

    def test_a_missing_project_is_reported_back(self, phab):
        result = _app(phab).show(["#development", "#nope"])

        assert len(result["projects"]) == 1
        assert result["missing_ids"] == ["#nope"]

    def test_the_cli_exits_non_zero_on_a_missing_project(
        self, phab, restore_output_format
    ):
        result = _invoke(
            phab, ["--format=json", "project", "show", "#development,#nope"]
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)[0]["Project"]["Name"] == "Development"


class TestSearch:
    def _constraints(self, phab):
        return phab.project.search.call_args.kwargs["constraints"]

    def test_active_only_by_default(self, phab):
        result = _app(phab).search()

        assert self._constraints(phab)["status"] == "active"
        assert "Old Project" not in [r["Project"]["Name"] for r in result["projects"]]

    def test_narrowed_to_phab_space_by_default(self, phab):
        """The way a task search is, so the two agree on where they look."""
        _app(phab).search()

        assert self._constraints(phab)["spaces"] == ["PHID-SPCE-S1"]

    def test_a_named_space_replaces_the_default(self, phab):
        _app(phab).search(spaces=["S2", "S3"])

        assert self._constraints(phab)["spaces"] == ["PHID-SPCE-S2", "PHID-SPCE-S3"]

    def test_star_is_every_space_and_no_constraint(self, phab, spaces):
        _app(phab).search(spaces=["*"])

        assert "spaces" not in self._constraints(phab)
        spaces.assert_not_called()

    def test_a_default_that_cannot_be_resolved_is_every_space(self, phab, spaces):
        spaces.side_effect = RuntimeError("no such space")

        _app(phab).search()

        assert "spaces" not in self._constraints(phab)

    def test_a_named_space_that_cannot_be_resolved_fails(self, phab, spaces):
        spaces.side_effect = PhabfiveDataException("no such space")

        with pytest.raises(PhabfiveDataException):
            _app(phab).search(spaces=["Nowhere"])

    def test_members_are_resolved_to_phids(self, phab):
        _app(phab).search(members=["@admin", "@deploybot"])

        assert self._constraints(phab)["members"] == [
            "PHID-USER-admin",
            "PHID-USER-deploybot",
        ]

    def test_parents_are_resolved_to_phids(self, phab):
        _app(phab).search(parents=["#development"], milestones=True)

        constraints = self._constraints(phab)
        assert constraints["parents"] == ["PHID-PROJ-12"]
        assert constraints["isMilestone"] is True

    def test_sorted_by_name_then_id(self, phab):
        result = _app(phab).search()

        assert [
            r["_url"].rstrip("/").rsplit("/", 1)[-1] for r in result["projects"]
        ] == [
            "12",
            "20",
            "14",
            "13",
            "15",
        ]

    def test_an_unknown_status_is_refused(self, phab):
        with pytest.raises(PhabfiveConfigException):
            _app(phab).search(status="closed")

    def test_any_is_what_phorge_calls_all(self, phab):
        _app(phab).search(status="any")

        assert self._constraints(phab)["status"] == "all"

    @pytest.mark.parametrize(
        "args", [["--all"], ["--status=any"], ["--all", "--status=any"]]
    )
    def test_all_is_a_deprecated_alias_for_status_any(
        self, phab, args, restore_output_format
    ):
        result = _invoke(phab, ["--format=json", "project", "search", *args])

        assert result.exit_code == 0, result.output
        assert self._constraints(phab)["status"] == "all"
        assert ("deprecated" in result.stderr) == ("--all" in args)

    def test_all_contradicting_status_is_refused(self, phab):
        result = _invoke(phab, ["project", "search", "--all", "--status=archived"])

        assert result.exit_code == 1
        assert "cannot be combined" in result.stderr

    def test_an_unknown_status_is_refused_before_any_call(self, phab):
        result = _invoke(phab, ["project", "search", "--status=closed"])

        assert result.exit_code == 1
        assert "--status must be one of: active, archived, any" in result.stderr
        phab.project.search.assert_not_called()

    def test_nothing_found_leaves_stdout_empty(self, phab, restore_output_format):
        result = _invoke(
            phab, ["--format=json", "project", "search", "--status=archived", "nope"]
        )

        assert result.exit_code == 0
        assert result.stdout == ""
        assert "No projects found" in result.stderr


class TestAudit:
    """`phabfive --format=jsonl project search --status=any --space='*' --show-policy -l 0`

    Every project on the instance, with its policy, one object per line -
    across pages, archived and milestones included, in one policy lookup.
    """

    ARGV = [
        "--format=jsonl",
        "project",
        "search",
        "--status=any",
        "--space=*",
        "--show-policy",
        "-l",
        "0",
    ]

    def test_every_project_on_one_line_each(self, restore_output_format):
        phab = FakePhab(ALL, users=[ADMIN], page_size=2)

        result = _invoke(phab, self.ARGV)

        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert len(lines) == len(ALL)

        records = [json.loads(line) for line in lines]
        assert all(
            set(r["Policy"]) == {"Visible To", "Editable By", "Joinable By"}
            for r in records
        )
        assert {r["Project"]["Name"] for r in records} >= {"Old Project", "Sprint 1"}

    def test_it_pages_through_everything_and_filters_nothing_away(
        self, restore_output_format
    ):
        phab = FakePhab(ALL, page_size=2)

        _invoke(phab, self.ARGV)

        assert phab.project.search.call_count == 3
        for call in phab.project.search.call_args_list:
            assert call.kwargs["constraints"] == {"status": "all"}

    def test_the_policies_cost_one_lookup(self, restore_output_format):
        phab = FakePhab(ALL, page_size=2)

        _invoke(phab, self.ARGV)

        policy_calls = [
            call
            for call in phab.phid.query.call_args_list
            if any(phid.startswith("PHID-PROJ-") for phid in call.kwargs["phids"])
        ]
        assert len(policy_calls) == 1


class TestCreate:
    def _build(self, phab, name="Platform", **options):
        return _app(phab).build_project_create(name, **options)

    def test_every_option_becomes_a_transaction(self, phab):
        transactions, _ = self._build(
            phab,
            description="Things",
            icon="infrastructure",
            color="red",
            slugs=["#plat", "platform-team"],
            members=["@admin", "@deploybot"],
            visible_to="users",
            editable_by="#humans",
            joinable_by="admin",
        )

        assert transactions == [
            {"type": "name", "value": "Platform"},
            {"type": "description", "value": "Things"},
            {"type": "icon", "value": "infrastructure"},
            {"type": "color", "value": "red"},
            {"type": "slugs", "value": ["plat", "platform-team"]},
            {
                "type": "members.add",
                "value": ["PHID-USER-admin", "PHID-USER-deploybot"],
            },
            {"type": "view", "value": "users"},
            {"type": "edit", "value": "PHID-PROJ-20"},
            {"type": "join", "value": "admin"},
        ]

    def test_a_policy_is_previewed_by_name(self, phab):
        _, changes = self._build(phab, editable_by="#humans", joinable_by="admin")

        assert {"field": "Editable By", "old": None, "new": "#humans"} in changes
        assert {"field": "Joinable By", "old": None, "new": "Administrators"} in changes

    def test_a_subproject(self, phab):
        transactions, changes = self._build(phab, parent="#development")

        assert {"type": "parent", "value": "PHID-PROJ-12"} in transactions
        assert {"field": "Parent", "old": None, "new": "#development"} in changes

    def test_a_milestone_may_share_its_name(self, phab):
        transactions, _ = self._build(phab, name="Sprint 1", milestone_of="#qa")

        assert {"type": "milestone", "value": "PHID-PROJ-14"} in transactions
        # No hashtag lookup for a milestone: it has none to collide with
        phab.project.search.assert_called_once_with(constraints={"slugs": ["qa"]})

    def test_a_taken_hashtag_is_refused_before_anything_is_sent(self, phab):
        with pytest.raises(PhabfiveDataException, match="same hashtag as #development"):
            self._build(phab, name="Development")

        phab.project.edit.assert_not_called()

    def test_parent_and_milestone_of_contradict(self, phab):
        with pytest.raises(PhabfiveConfigException, match="cannot be combined"):
            self._build(phab, parent="#qa", milestone_of="#qa")

    @pytest.mark.parametrize("option", [{"icon": "tag"}, {"slugs": ["x"]}])
    def test_a_milestone_takes_no_icon_or_hashtag(self, phab, option):
        """Phorge ignores the one and never reports the other."""
        with pytest.raises(PhabfiveConfigException, match="milestone takes no"):
            self._build(phab, name="Sprint 2", milestone_of="#qa", **option)

    def test_an_unknown_member_names_itself(self, phab):
        with pytest.raises(PhabfiveDataException, match="'@ghost'"):
            self._build(phab, members=["@admin", "@ghost"])

    def test_a_lockout_is_reported_as_a_sentence(self, phab):
        from phabfive.exceptions import PhabfiveAPIException

        phab.project.edit.side_effect = PhabfiveAPIException(
            "ERR-CONDUIT-CORE",
            "Validation errors:\n  - The edit policy of this object would no "
            "longer allow you to edit the object.",
        )

        with pytest.raises(PhabfiveDataException, match="Nothing was changed"):
            _app(phab).apply_project_create([{"type": "name", "value": "x"}])


class TestEdit:
    def _build(self, phab, ident="#humans", **options):
        project = _app(phab)
        return project.build_project_edit(
            project.get_project_for_edit(ident), **options
        )

    def test_nothing_to_change_is_no_transaction(self, phab):
        transactions, changes = self._build(
            phab,
            name="Humans",
            color="blue",
            add_members=["@admin"],
            visible_to="users",
            joinable_by="admin",
        )

        assert transactions == []
        assert changes == []

    def test_only_the_members_not_already_in_are_added(self, phab):
        phab.users["PHID-USER-viola"] = _user("viola")

        transactions, changes = self._build(phab, add_members=["@admin", "@viola"])

        assert transactions == [{"type": "members.add", "value": ["PHID-USER-viola"]}]
        assert changes == [{"field": "Members", "old": None, "new": "Added: @viola"}]

    def test_only_members_who_are_in_are_removed(self, phab):
        phab.users["PHID-USER-viola"] = _user("viola")

        transactions, _ = self._build(phab, remove_members=["@deploybot", "@viola"])

        assert transactions == [
            {"type": "members.remove", "value": ["PHID-USER-deploybot"]}
        ]

    def test_adding_and_removing_the_same_member_is_refused(self, phab):
        with pytest.raises(PhabfiveConfigException, match="both add and remove"):
            self._build(phab, add_members=["@admin"], remove_members=["@admin"])

    def test_adding_a_hashtag_keeps_the_ones_already_there(self, phab):
        """The slugs transaction replaces the list, so the list is sent whole."""
        phab.slugs["PHID-PROJ-20"] = ["people"]

        transactions, changes = self._build(phab, add_slugs=["#folk", "people"])

        assert transactions == [
            {"type": "slugs", "value": ["humans", "people", "folk"]}
        ]
        assert changes == [{"field": "Hashtags", "old": None, "new": "Added: #folk"}]

    def test_a_policy_change_names_both_ends(self, phab):
        transactions, changes = self._build(phab, editable_by="admin")

        assert transactions == [{"type": "edit", "value": "admin"}]
        assert changes == [
            {"field": "Editable By", "old": "#humans", "new": "Administrators"}
        ]

    def test_a_rename_into_a_taken_hashtag_is_refused(self, phab):
        with pytest.raises(PhabfiveDataException, match="same hashtag"):
            self._build(phab, name="Development")

    def test_a_milestone_takes_no_icon(self, phab):
        with pytest.raises(PhabfiveConfigException, match="is a milestone"):
            self._build(phab, ident="13", icon="tag")

    def test_no_option_is_a_usage_error(self, phab):
        result = _invoke(phab, ["project", "edit", "#humans"])

        assert result.exit_code == 1
        phab.project.edit.assert_not_called()

    def test_a_bad_policy_is_refused_before_the_instance_is_reached(self, phab):
        result = _invoke(phab, ["project", "edit", "#humans", "--joinable-by=nonsense"])

        assert result.exit_code == 1
        assert "--joinable-by must be one of" in result.stderr
        phab.project.search.assert_not_called()


MACHINE = ["yaml", "json", "jsonl"]
HUMAN = ["rich", "tree", "table", "value"]


class TestWriteFormats:
    """A machine format answers a write with the `project show` record (#344)."""

    @pytest.mark.parametrize("output_format", MACHINE + HUMAN)
    def test_create(self, phab, output_format, restore_output_format):
        phab.project.edit.return_value = {"object": {"id": 12, "phid": "PHID-PROJ-12"}}

        result = _invoke(
            phab, [f"--format={output_format}", "project", "create", "Platform"]
        )

        assert result.exit_code == 0, result.output
        if is_machine_format(output_format):
            assert "Development" in result.stdout
            assert "Name: Platform" in result.stderr
        else:
            assert "Name: Platform" in result.stdout
            assert "Development" not in result.stdout

    def test_json_create_parses_on_its_own(self, phab, restore_output_format):
        phab.project.edit.return_value = {"object": {"id": 12, "phid": "PHID-PROJ-12"}}

        result = _invoke(phab, ["--format=json", "project", "create", "Platform"])

        [record] = json.loads(result.stdout)
        assert record["Link"] == f"{URL}/project/view/12/"

    def test_dry_run_leaves_stdout_empty(self, phab, restore_output_format):
        result = _invoke(
            phab, ["--format=json", "project", "create", "Platform", "--dry-run"]
        )

        assert result.exit_code == 0
        assert result.stdout == ""
        assert "[DRY RUN] Would create Platform:" in result.stderr
        phab.project.edit.assert_not_called()

    def test_edit_emits_the_record_by_id(self, phab, restore_output_format):
        result = _invoke(
            phab, ["--format=json", "project", "edit", "#humans", "--name=People"]
        )

        assert result.exit_code == 0, result.output
        phab.project.edit.assert_called_once_with(
            transactions=[{"type": "name", "value": "People"}],
            objectIdentifier="PHID-PROJ-20",
        )
        [record] = json.loads(result.stdout)
        assert record["Link"] == f"{URL}/project/view/20/"

    def test_an_edit_needing_nothing_still_has_a_record(
        self, phab, restore_output_format
    ):
        result = _invoke(
            phab, ["--format=json", "project", "edit", "#humans", "--color=blue"]
        )

        assert result.exit_code == 0
        assert "No changes" in result.stderr
        assert json.loads(result.stdout)[0]["Project"]["Name"] == "Humans"
        phab.project.edit.assert_not_called()

    def test_edit_dry_run_leaves_stdout_empty(self, phab, restore_output_format):
        result = _invoke(
            phab,
            ["--format=json", "project", "edit", "#humans", "--color=red", "--dry-run"],
        )

        assert result.stdout == ""
        assert "Color: blue → red" in result.stderr
        phab.project.edit.assert_not_called()

    def test_a_write_drops_the_cached_project_completions(self, phab):
        with patch("phabfive.cli.project.forget_projects") as forget:
            _invoke(phab, ["project", "edit", "#humans", "--icon=tag"])

        forget.assert_called_once_with(icons=True)

    def test_a_dry_run_leaves_the_cache_alone(self, phab):
        with patch("phabfive.cli.project.forget_projects") as forget:
            _invoke(phab, ["project", "edit", "#humans", "--icon=tag", "--dry-run"])

        forget.assert_not_called()


def _milestone(pid, parent, stored_color, number=1):
    """A milestone as Phorge reports it: the milestone icon and its parent's
    colour in its fields, with whatever was stored on it kept apart."""
    project = _project(
        pid,
        f"Sprint {number}",
        parent=parent,
        milestone=number,
        icon="milestone",
        color=parent["fields"]["color"]["key"],
    )
    project["_stored"] = {"icon": "project", "color": stored_color}
    return project


class TestSearchByLook:
    """--icon and --color match what Phorge shows, milestones included."""

    GREEN = _project(40, "Green Team", slug="green_team", color="green", icon="group")
    RED = _project(41, "Red Team", slug="red_team", color="red")
    ARCHIVED_RED = _project(
        42, "Old Red", slug="old_red", color="disabled", status="archived"
    )
    # Stored blue, shown green: the case the server gets wrong
    GREEN_SPRINT = _milestone(43, GREEN, stored_color="blue")
    # Stored green, shown red
    RED_SPRINT = _milestone(44, RED, stored_color="green")
    ARCHIVED_SPRINT = _milestone(45, ARCHIVED_RED, stored_color="blue")

    @pytest.fixture
    def phab(self):
        self.ARCHIVED_RED["_stored"] = {"color": "red"}
        return FakePhab(
            [
                self.GREEN,
                self.RED,
                self.ARCHIVED_RED,
                self.GREEN_SPRINT,
                self.RED_SPRINT,
                self.ARCHIVED_SPRINT,
            ],
            users=[ADMIN],
        )

    def _names(self, phab, **kwargs):
        result = _app(phab).search(spaces=["*"], **kwargs)
        return sorted(
            (r["Project"]["Name"], r["_url"].rstrip("/").rsplit("/", 1)[-1])
            for r in result["projects"]
        )

    def test_a_milestone_is_found_by_the_colour_it_is_shown_in(self, phab):
        assert self._names(phab, colors=["green"]) == [
            ("Green Team", "40"),
            ("Sprint 1", "43"),
        ]

    def test_a_milestone_is_not_found_by_its_hidden_stored_colour(self, phab):
        assert self._names(phab, colors=["blue"]) == []

    def test_an_archived_parent_keeps_the_colour_it_was_given(self, phab):
        """Phorge shows an archived project as "disabled"; its milestones
        still take the colour it was given."""
        assert self._names(phab, colors=["red"], status="any") == [
            ("Old Red", "42"),
            ("Red Team", "41"),
            ("Sprint 1", "44"),
            ("Sprint 1", "45"),
        ]

    def test_colours_or(self, phab):
        # The active ones: both teams and all three milestones, the archived
        # parent's included - the milestone itself is active
        assert len(self._names(phab, colors=["green", "red"])) == 5

    def test_milestone_icon_finds_milestones(self, phab):
        assert [pid for _, pid in self._names(phab, icons=["milestone"])] == [
            "43",
            "44",
            "45",
        ]

    def test_a_milestone_is_not_found_by_its_hidden_stored_icon(self, phab):
        assert self._names(phab, icons=["project"]) == [("Red Team", "41")]

    def test_icon_and_colour_and(self, phab):
        assert self._names(phab, icons=["group"], colors=["green"]) == [
            ("Green Team", "40")
        ]
        assert self._names(phab, icons=["milestone"], colors=["green"]) == [
            ("Sprint 1", "43")
        ]

    def test_no_milestones(self, phab):
        assert self._names(phab, colors=["green"], milestones=False) == [
            ("Green Team", "40")
        ]

    def test_only_milestones(self, phab):
        assert self._names(phab, colors=["green"], milestones=True) == [
            ("Sprint 1", "43")
        ]

    def test_the_limit_applies_to_both_searches_together(self, phab):
        assert len(self._names(phab, colors=["green"], limit=1)) == 1

    def test_without_icon_or_colour_it_is_one_search(self, phab):
        _app(phab).search(spaces=["*"])

        assert phab.project.search.call_count == 1

    def test_an_unknown_colour_is_refused_before_any_call(self, phab):
        with pytest.raises(PhabfiveConfigException, match="Unknown project color"):
            _app(phab).search(colors=["green", "grean"])

        phab.project.search.assert_not_called()

    def test_disabled_is_not_a_colour_to_ask_for(self, phab):
        with pytest.raises(PhabfiveConfigException, match="expected one of"):
            _app(phab).search(colors=["disabled"])

    def test_the_cli_reports_an_unknown_colour(self, phab):
        result = _invoke(phab, ["project", "search", "--color=grean"])

        assert result.exit_code == 1
        assert "Unknown project color 'grean'" in result.stderr


class TestColourOnWrites:
    def test_create_refuses_an_unknown_colour(self, phab):
        with pytest.raises(PhabfiveConfigException, match="Unknown project color"):
            _app(phab).build_project_create("New", color="grean")

    def test_create_refuses_a_colour_on_a_milestone(self, phab):
        """Phorge would store it, show the parent's colour instead, and let a
        search find the milestone under the stored one."""
        with pytest.raises(PhabfiveConfigException, match="--color"):
            _app(phab).build_project_create(
                "Sprint 2", milestone_of="#development", color="red"
            )

    def test_edit_refuses_a_colour_on_a_milestone(self, phab):
        app = _app(phab)
        milestone = app.get_project("13")

        with pytest.raises(PhabfiveConfigException, match="is a milestone"):
            app.build_project_edit(milestone, color="red")

    def test_edit_refuses_an_unknown_colour(self, phab):
        app = _app(phab)

        with pytest.raises(PhabfiveConfigException, match="Unknown project color"):
            app.build_project_edit(app.get_project("#humans"), color="grean")
