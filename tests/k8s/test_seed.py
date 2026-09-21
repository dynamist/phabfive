# -*- coding: utf-8 -*-
"""The seed modules created what docs/phorge-setup.md promises."""


def test_users(conduit, seed, admin_username):
    usernames = [admin_username, *(user["username"] for user in seed["users"])]
    found = conduit("user.search", constraints={"usernames": usernames})["data"]
    assert sorted(user["fields"]["username"] for user in found) == sorted(usernames)


def test_bot_and_disabled_users(conduit, seed):
    """The seed flags an account as a bot or disabled, and Phorge reports it."""
    flagged = [
        user for user in seed["users"] if user.get("bot") or user.get("disabled")
    ]
    assert flagged, "the seed has no bot or disabled account to filter on"

    found = conduit(
        "user.search",
        constraints={"usernames": [user["username"] for user in flagged]},
    )["data"]
    roles = {user["fields"]["username"]: user["fields"]["roles"] for user in found}

    for user in flagged:
        assert ("bot" in roles[user["username"]]) == bool(user.get("bot"))
        assert ("disabled" in roles[user["username"]]) == bool(user.get("disabled"))


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


def test_credentials(conduit, seed):
    """Passphrase has no create method, so the module writes through Phorge's
    editors - these assert it wrote what the data file says."""
    found = conduit("passphrase.query", limit=100)["data"]
    by_name = {credential["name"]: credential for credential in found.values()}

    for record in seed["credentials"]:
        assert record["name"] in by_name, f"credential {record['name']} missing"
        credential = by_name[record["name"]]
        assert credential["type"] == record["type"], record["name"]
        assert credential["username"] == record.get("username", ""), record["name"]


def test_credentials_are_numbered_in_data_file_order(conduit, seed):
    """The K numbers follow the file, which is what lets docs name K1 and K2."""
    found = conduit("passphrase.query", limit=100)["data"]
    by_name = {credential["name"]: credential for credential in found.values()}

    ids = [int(by_name[record["name"]]["id"]) for record in seed["credentials"]]
    assert ids == sorted(ids)


def test_a_credential_without_conduit_access_hides_its_secret(conduit, seed):
    """Deliberate: one credential phabfive cannot read the secret of.

    Attaching a credential to a repository URI needs only its PHID and type,
    both of which passphrase.query returns either way, so this is what tells
    a path that reads the secret apart from one that does not.
    """
    found = conduit("passphrase.query", limit=100, needSecrets=1)["data"]
    by_name = {credential["name"]: credential for credential in found.values()}

    blocked = [record for record in seed["credentials"] if not record["conduit"]]
    assert blocked, "no credential has Conduit access turned off"
    for record in blocked:
        assert "noAPIAccess" in by_name[record["name"]]["material"], (
            f"{record['name']} should not be readable over Conduit"
        )

    # A credential of the same type that is readable, or the assertion above
    # would also pass on an instance where nothing at all can be read
    types = {record["type"] for record in blocked}
    readable = [
        record
        for record in seed["credentials"]
        if record["conduit"] and record["type"] in types
    ]
    assert readable, "nothing of the same type is readable, so nothing is compared"
    for record in readable:
        assert "privateKey" in by_name[record["name"]]["material"], record["name"]


def test_repositories(conduit, seed):
    found = conduit("diffusion.repository.search", limit=100)["data"]
    by_callsign = {repo["fields"]["callsign"]: repo for repo in found}

    for record in seed["repositories"]["repositories"]:
        assert record["callsign"] in by_callsign, f"r{record['callsign']} missing"
        fields = by_callsign[record["callsign"]]["fields"]
        assert fields["vcs"] == "git"
        assert fields["status"] == "active"
        # Hosted, so nothing about these repositories needs the network
        assert fields["isHosted"], record["callsign"]


def test_branches_and_tags_are_on_disk(conduit, seed):
    """The refs are there as soon as the instance answers.

    A hosted repository's working copy is normally created by the daemons,
    asynchronously; the seeder creates it and writes the history itself,
    before Apache starts, so this needs no waiting and cannot flake.
    """
    found = conduit("diffusion.repository.search", limit=100)["data"]
    by_callsign = {repo["fields"]["callsign"]: repo for repo in found}

    for record in seed["repositories"]["repositories"]:
        repository = by_callsign[record["callsign"]]["id"]
        branches = conduit("diffusion.branchquery", repository=repository)
        tags = conduit("diffusion.tagsquery", repository=repository)

        expected_branches = {branch["name"] for branch in record.get("branches", [])}
        expected_tags = {
            commit["tag"]
            for branch in record.get("branches", [])
            for commit in branch["commits"]
            if "tag" in commit
        }

        assert {branch["shortName"] for branch in branches} == expected_branches
        assert {tag["name"] for tag in tags} == expected_tags


def test_one_repository_is_empty_and_one_has_history(seed):
    """Both paths are covered, or the tests above prove less than they look."""
    records = seed["repositories"]["repositories"]
    assert [bool(record.get("branches")) for record in records].count(True) >= 1
    assert [bool(record.get("branches")) for record in records].count(False) >= 1
