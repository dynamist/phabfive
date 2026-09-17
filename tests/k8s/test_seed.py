# -*- coding: utf-8 -*-
"""The init scripts created what docs/phorge-setup.md promises."""


def test_users(conduit, seed):
    usernames = ["admin", *(user[0] for user in seed["FAKE_USERS"])]
    found = conduit("user.search", constraints={"usernames": usernames})["data"]
    assert sorted(user["fields"]["username"] for user in found) == sorted(usernames)


def test_projects_and_milestones(conduit, seed):
    projects = conduit("project.search", limit=100)["data"]
    by_name = {}
    for project in projects:
        by_name.setdefault(project["fields"]["name"], []).append(project)

    for name, _description in seed["DEFAULT_PROJECTS"]:
        assert name in by_name, f"project {name} missing"

    parents = {project["phid"]: project["fields"]["name"] for project in projects}
    for parent_name, milestone_name in seed["DEFAULT_MILESTONES"]:
        milestones = [
            project
            for project in by_name.get(milestone_name, [])
            if project["fields"]["milestone"]
            and parents.get(project["fields"]["parent"]["phid"]) == parent_name
        ]
        assert milestones, f"milestone {milestone_name} of {parent_name} missing"


def test_spaces(conduit, seed):
    names = [f"S{space[0]}" for space in seed["DEFAULT_SPACES"]]
    found = conduit("phid.lookup", names=names)
    assert sorted(found) == sorted(names)
