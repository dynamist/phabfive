# -*- coding: utf-8 -*-
"""The seed modules created what docs/phorge-setup.md promises."""

# 3rd party imports
import pytest

from tests.phorge_versions import phorge_older_than


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


def test_dynatron_is_a_bot_and_not_an_admin(conduit):
    """Dynatron's standup page calls Conduit as dynatron, not as the admin."""
    [found] = conduit("user.search", constraints={"usernames": ["dynatron"]})["data"]
    roles = found["fields"]["roles"]

    assert "bot" in roles
    assert "admin" not in roles
    assert "disabled" not in roles


def test_dynatron_token_reads_as_dynatron(conduit, dynatron_token):
    """What a bot gets by default is enough for what the standup page asks."""
    me = conduit("user.whoami", token=dynatron_token)
    assert me["userName"] == "dynatron"
    assert "admin" not in me["roles"]

    # Any logged-in user sees the seeded tasks, so the bot does too
    as_admin = conduit("maniphest.search", limit=5)["data"]
    as_dynatron = conduit("maniphest.search", token=dynatron_token, limit=5)["data"]
    assert as_dynatron
    assert [task["phid"] for task in as_dynatron] == [task["phid"] for task in as_admin]


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


def test_hidden_and_milestone_columns(conduit, seed):
    """A board with a hidden column and a milestone's column, for #588.

    Phorge creates a milestone's column on its parent's board only when the
    board is first drawn, so without the seed creating it project.column.search
    would not list it on an instance nobody has opened.
    """
    projects = conduit("project.search", limit=100)["data"]
    tops = {
        project["fields"]["name"]: project
        for project in projects
        if not project["fields"]["milestone"]
    }

    for record in seed["projects"]["projects"]:
        hidden = record.get("hidden_columns", [])
        milestones = [milestone["name"] for milestone in record.get("milestones", [])]
        if not hidden and not milestones:
            continue

        board = tops[record["name"]]
        columns = conduit(
            "project.column.search", constraints={"projects": [board["phid"]]}
        )["data"]
        by_name = {column["fields"]["name"]: column["fields"] for column in columns}

        for name in hidden:
            # Reported only from 2023.32 on; the column exists either way
            if phorge_older_than("2023.32"):
                assert name in by_name, (record["name"], name)
            else:
                assert by_name[name]["isHidden"] is True, (record["name"], name)

        children = {
            project["phid"]
            for project in projects
            if project["fields"]["milestone"]
            and project["fields"]["parent"]["phid"] == board["phid"]
        }
        proxies = {column["fields"]["proxyPHID"] for column in columns} - {None}
        assert proxies == children, record["name"]

        for name in milestones:
            assert by_name[name]["proxyPHID"] in children, (record["name"], name)


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
        # Hosted, so nothing about these repositories needs the network. Only
        # Phorge 2025.51 and newer report it
        if not phorge_older_than("2025.51"):
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


SEED_SCRIPT = "/usr/local/share/phabfive-seed/seed.php"
TOKEN_EXPIRES = (
    "SELECT IFNULL(expires, 'never') FROM conduit_token WHERE token = :token"
)


# The seed module that makes each well-known token, and the variable it reads
TOKENS = {"auth": "PHORGE_ADMIN_TOKEN", "dynatron": "PHORGE_DYNATRON_TOKEN"}


def token_sql(kubectl, statement, variable="PHORGE_ADMIN_TOKEN"):
    """Run SQL on phabricator_conduit from the Phorge pod.

    `:token` in `statement` is the token in `variable`, quoted. The shell fills
    it in from the pod's environment, so the test cannot disagree with the seed.
    """
    statement = statement.replace(":token", f"'${variable}'")
    script = (
        'mysql -h"$MYSQL_HOST" -u"$MYSQL_USER" -p"$MYSQL_PASS" -N '
        f'-D phabricator_conduit -e "{statement}"'
    )
    return kubectl("exec", "deploy/phorge", "--", "sh", "-c", script).stdout.strip()


def run_seed(kubectl, module):
    return kubectl("exec", "deploy/phorge", "--", "php", SEED_SCRIPT, module).stdout


@pytest.mark.parametrize("module", sorted(TOKENS))
def test_api_token_never_expires(kubectl, module):
    """The seed's token is permanent, whether it creates it or finds it.

    A "cli" token expires an hour after it is made unless it is used, and the
    daemons then delete it, which is how an idle instance lost its token. Using
    the token also makes it permanent, so by now the stored row says nothing
    about the seed: this deletes the token and runs the seed again, then gives
    it an expiry and runs the seed again.
    """
    variable = TOKENS[module]
    try:
        token_sql(kubectl, "DELETE FROM conduit_token WHERE token = :token", variable)
        run_seed(kubectl, module)
        assert token_sql(kubectl, TOKEN_EXPIRES, variable) == "never", (
            "created with an expiry"
        )

        token_sql(
            kubectl,
            "UPDATE conduit_token SET expires = UNIX_TIMESTAMP() + 3600 "
            "WHERE token = :token",
            variable,
        )
        run_seed(kubectl, module)
        assert token_sql(kubectl, TOKEN_EXPIRES, variable) == "never", (
            "expiry left in place"
        )
    finally:
        # The seed creates the token again if a step above lost it
        run_seed(kubectl, module)
        token_sql(
            kubectl,
            "UPDATE conduit_token SET expires = NULL WHERE token = :token",
            variable,
        )


def test_the_dynatron_seed_runs_twice(kubectl, conduit, dynatron_token):
    """Idempotent: a second run creates nothing and changes nothing."""
    run_seed(kubectl, "dynatron")
    before = conduit("user.search", constraints={"usernames": ["dynatron"]})["data"]
    tokens = token_sql(
        kubectl,
        "SELECT COUNT(*) FROM conduit_token WHERE token = :token",
        "PHORGE_DYNATRON_TOKEN",
    )

    output = run_seed(kubectl, "dynatron")

    assert "Created" not in output and "Made" not in output, output
    assert (
        conduit("user.search", constraints={"usernames": ["dynatron"]})["data"]
        == before
    )
    assert tokens == "1"
    assert (
        token_sql(
            kubectl,
            "SELECT COUNT(*) FROM conduit_token WHERE token = :token",
            "PHORGE_DYNATRON_TOKEN",
        )
        == "1"
    )
    assert conduit("user.whoami", token=dynatron_token)["userName"] == "dynatron"


def _epoch(text):
    """An ISO 8601 date with a Z, as phorge/seed/data/stats.json writes it."""
    import datetime

    return int(datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _metrics_tasks(conduit, seed, **constraints):
    [project] = conduit(
        "project.search", constraints={"name": seed["stats"]["project"]["name"]}
    )["data"]
    found = conduit(
        "maniphest.search",
        constraints={"projects": [project["phid"]], **constraints},
        limit=100,
    )["data"]
    return {task["fields"]["name"]: task["fields"] for task in found}


def test_stats_tasks_are_at_known_dates(conduit, seed):
    """The `stats` module backdated its tasks, for `maniphest stats` (#598).

    Conduit cannot backdate, so the module writes dateCreated and closedEpoch
    to the row after creating each task. A reopened task has no close date.
    """
    found = _metrics_tasks(conduit, seed)

    for record in seed["stats"]["tasks"]:
        fields = found[record["title"]]
        assert fields["dateCreated"] == _epoch(record["created"]), record["title"]

        if "closed" in record:
            assert fields["dateClosed"] == _epoch(record["closed"]), record["title"]
            assert fields["status"]["value"] == record["status"]
        else:
            assert fields["dateClosed"] is None, record["title"]
            assert fields["status"]["value"] == "open"


def test_stats_tasks_are_found_by_the_date_constraints(conduit, seed):
    """createdStart/End and closedStart/End compare against those columns."""
    january = {
        "Start": _epoch("2025-01-01T00:00:00Z"),
        "End": _epoch("2025-02-01T00:00:00Z"),
    }

    created = _metrics_tasks(
        conduit,
        seed,
        createdStart=january["Start"],
        createdEnd=january["End"],
    )
    closed = _metrics_tasks(
        conduit,
        seed,
        closedStart=january["Start"],
        closedEnd=january["End"],
    )

    def expected(key):
        return {
            record["title"]
            for record in seed["stats"]["tasks"]
            if key in record
            and january["Start"] <= _epoch(record[key]) < january["End"]
        }

    assert set(created) == expected("created")
    assert set(closed) == expected("closed")


def test_the_stats_seed_runs_twice(kubectl, conduit, seed):
    """Idempotent: a second run creates nothing and leaves the dates alone."""
    before = _metrics_tasks(conduit, seed)

    result = kubectl("exec", "deploy/phorge", "--", "php", SEED_SCRIPT, "stats")

    assert "Created 0 task(s)" in result.stdout
    assert _metrics_tasks(conduit, seed) == before
