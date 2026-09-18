# -*- coding: utf-8 -*-
"""The seed modules created what docs/phorge-setup.md promises."""


def test_users(conduit, seed, admin_username):
    usernames = [admin_username, *(user["username"] for user in seed["users"])]
    found = conduit("user.search", constraints={"usernames": usernames})["data"]
    assert sorted(user["fields"]["username"] for user in found) == sorted(usernames)


def test_projects_and_milestones(conduit, seed):
    projects = conduit("project.search", limit=100)["data"]
    by_name = {}
    for project in projects:
        by_name.setdefault(project["fields"]["name"], []).append(project)

    for project in seed["projects"]["projects"]:
        assert project["name"] in by_name, f"project {project['name']} missing"

    parents = {project["phid"]: project["fields"]["name"] for project in projects}
    for parent in seed["projects"]["projects"]:
        for milestone in parent.get("milestones", []):
            found = [
                project
                for project in by_name.get(milestone["name"], [])
                if project["fields"]["milestone"]
                and parents.get(project["fields"]["parent"]["phid"]) == parent["name"]
            ]
            assert found, f"milestone {milestone['name']} of {parent['name']} missing"


def test_teams(conduit, seed):
    names = [team["name"] for team in seed["teams"]]
    # project.search has no "names" constraint, so the list is filtered here
    found = conduit("project.search", limit=100)["data"]
    assert set(names) <= {project["fields"]["name"] for project in found}


def test_team_members(conduit, seed, admin_username):
    """A team's members are what decides who sees its Space."""
    projects = conduit("project.search", limit=100, attachments={"members": 1})["data"]
    by_name = {project["fields"]["name"]: project for project in projects}

    for team in seed["teams"]:
        found = by_name[team["name"]]
        phids = [
            member["phid"] for member in found["attachments"]["members"]["members"]
        ]
        usernames = [
            user["fields"]["username"]
            for user in conduit("user.search", constraints={"phids": phids})["data"]
        ]
        expected = list(team["members"])
        if team.get("admin"):
            expected.append(admin_username)
        assert sorted(usernames) == sorted(expected), team["name"]


def test_spaces_follow_the_data_file_order(conduit, seed):
    """The S number is the position in the file, so gaps are access, not numbering."""
    monograms = [f"S{number}" for number, _ in enumerate(seed["spaces"], start=1)]
    found = conduit("phid.lookup", names=monograms)

    for monogram, space in zip(monograms, seed["spaces"]):
        if monogram in found:
            assert found[monogram]["name"] == space["name"]


def test_a_team_space_is_invisible_to_the_admin(conduit, seed, admin_username):
    """phid.lookup omits a Space the viewer cannot see, which is why probing
    a range cannot stop at the first miss."""
    admin_teams = {team["name"] for team in seed["teams"] if team.get("admin")}

    visible, hidden = [], []
    for number, space in enumerate(seed["spaces"], start=1):
        team = space.get("team")
        target = visible if (team is None or team in admin_teams) else hidden
        target.append(f"S{number}")

    found = conduit("phid.lookup", names=visible + hidden)

    assert sorted(found) == sorted(visible)
    assert hidden, "no Space is hidden from the admin, so nothing tests the gaps"
    assert max(int(m[1:]) for m in visible) > min(int(m[1:]) for m in hidden), (
        "every hidden Space is past the last visible one, so no gap is probed"
    )
