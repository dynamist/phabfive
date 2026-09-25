# -*- coding: utf-8 -*-
"""The phabfive CLI against a live Phorge, see docs/phorge-setup.md."""

# python std lib
import json
import time
import uuid
from urllib.parse import urlparse

# 3rd party imports
import requests
from ruamel.yaml import YAML


def test_whoami(phabfive, live_env):
    [host] = phabfive("user", "whoami", json_output=True)
    assert host["User"]["UserName"] == "admin"
    assert host["Host"] == urlparse(live_env["PHAB_URL"]).hostname


def test_create_on_a_workboard(phabfive, create_task):
    task_id, title = create_task("--tag", "Development", "--priority", "high")
    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert task["Task"]["Name"] == title
    assert task["Task"]["Priority"] == "High"
    assert task["Task"]["Status"] == "Open"
    assert task["Boards"]["Development"]["Column"] == "Backlog"


def test_edit_status(phabfive, create_task):
    task_id, _title = create_task()
    phabfive("maniphest", "edit", task_id, "--status", "resolved")
    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert task["Task"]["Status"] == "Resolved"


def _subscribers(conduit, method, object_id):
    """The usernames subscribed to one task or paste, read off Conduit."""
    [record] = conduit(
        method, **{"constraints[ids][0]": object_id, "attachments[subscribers]": 1}
    )["data"]
    phids = record["attachments"]["subscribers"]["subscriberPHIDs"]
    if not phids:
        return set()
    users = conduit(
        "user.search", **{f"constraints[phids][{i}]": p for i, p in enumerate(phids)}
    )["data"]
    return {user["fields"]["username"] for user in users}


def test_edit_adds_and_removes_task_subscribers(phabfive, conduit, create_task):
    task_id, _title = create_task("--add-subscriber=@viola.larsson")
    number = int(task_id[1:])
    # Creating a task subscribes its author
    assert _subscribers(conduit, "maniphest.search", number) == {
        "admin",
        "viola.larsson",
    }

    phabfive(
        "maniphest",
        "edit",
        task_id,
        "--add-subscriber=@mikael.wallin",
        "--remove-subscriber=@me,@viola.larsson",
    )

    assert _subscribers(conduit, "maniphest.search", number) == {"mikael.wallin"}


def test_edit_unassigns_a_task(phabfive, conduit, create_task):
    """maniphest.edit takes a null owner as "unassign", which a mocked client
    would accept whatever it meant."""
    task_id, _title = create_task("--assign=@viola.larsson")
    number = int(task_id[1:])

    def owner():
        [record] = conduit("maniphest.search", **{"constraints[ids][0]": number})[
            "data"
        ]
        return record["fields"]["ownerPHID"]

    assert owner() is not None

    phabfive("edit", task_id, "--unassign", "--yes")

    assert owner() is None


def test_search_by_tag(phabfive, create_task):
    _task_id, title = create_task("--tag", "QA")
    tasks = phabfive("maniphest", "search", "--tag", "QA", json_output=True)
    assert title in [task["Task"]["Name"] for task in tasks]


def test_create_in_a_space(phabfive, create_task, space_name):
    task_id, _title = create_task("--space", "S3")
    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert task["Space"] == space_name("S3")


def test_task_edit_sets_the_two_policies_it_can(
    phabfive, phabfive_raw, conduit, create_task
):
    """The task policy transaction names, against a real Phorge.

    This is the test that could not be written below the CLI. maniphest.edit
    takes `view` and `edit` and refuses `policy.view` and `policy.edit`, and a
    wrong name is an ERR-CONDUIT-CORE from the instance, which a mocked client
    cannot produce and would happily accept.

    It also pins the one thing tasks do not have: there is no interact
    transaction of any spelling. A task derives "Can Interact With" from its
    view policy - which is why it moves here without having been asked for -
    so a `--interact` option would be offering a write that cannot happen.
    """
    task_id, _title = create_task()
    slug = conduit("project.search", limit=1)["data"][0]["fields"]["slug"]
    me = conduit("user.whoami")["userName"]

    phabfive(
        "maniphest",
        "edit",
        task_id,
        f"--visible-to=#{slug}",
        f"--editable-by=@{me}",
        "--yes",
    )

    [task] = phabfive("maniphest", "show", task_id, "--show-policy", json_output=True)

    # Read back in the same spelling that set them, and "Can Interact"
    # following "Visible To" without having been named
    assert task["Policy"] == {
        "Visible To": f"#{slug}",
        "Editable By": f"@{me}",
        "Can Interact": f"#{slug}",
    }

    # Asking for what is already there is not a change. The sentence saying
    # so is prose, so under a machine-readable format it is on stderr (#344)
    # and stdout carries the task's record - "already at the target state"
    # is an answer about the task, not an absence of one.
    result = phabfive_raw(
        "--format",
        "json",
        "maniphest",
        "edit",
        task_id,
        f"--visible-to=#{slug}",
        "--yes",
    )

    assert result.returncode == 0
    assert "No changes" in result.stderr
    assert json.loads(result.stdout)[0]["Link"].endswith(f"/{task_id}")

    # And Phorge refuses to let the viewer lock themselves out, which is
    # reported as the sentence it answered with
    result = phabfive_raw("maniphest", "edit", task_id, "--visible-to=no-one", "--yes")

    assert result.returncode == 1
    assert "would no longer allow you" in result.stderr
    assert "Traceback" not in result.stderr


def test_maniphest_edit_has_no_interact_transaction(live_env):
    """Why there is no `--interact` option, asked of the instance rather than
    assumed from the read side.

    maniphest.edit answers an unknown transaction type by listing every valid
    one. That list holds `view` and `edit` and nothing for interact, in any
    spelling. ManiphestTask::getPolicy is why: a task does not store an
    interact policy, it returns its view policy unless its status locks
    comments, and then "no-one".

    Nothing is created - the type is rejected before any object is.
    """
    response = requests.post(
        live_env["PHAB_URL"].rstrip("/") + "/maniphest.edit",
        data={
            "api.token": live_env["PHAB_TOKEN"],
            "transactions[0][type]": "interact",
            "transactions[0][value]": "public",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    assert payload["error_code"] == "ERR-CONDUIT-CORE"
    assert 'invalid type "interact"' in payload["error_info"]

    valid = payload["error_info"].split("Valid types are: ")[1].rstrip(".").split(", ")

    assert "view" in valid
    assert "edit" in valid
    assert not [name for name in valid if "interact" in name]


def test_a_policy_outside_the_grammar_never_reaches_the_instance(
    phabfive_raw, create_task
):
    """Conduit reads a value it does not recognise as a policy nobody
    satisfies, so a typo would otherwise be answered as a permissions error."""
    task_id, _title = create_task()

    result = phabfive_raw(
        "maniphest", "edit", task_id, "--visible-to=nonsense", "--yes"
    )

    assert result.returncode == 1
    assert "--visible-to must be one of" in result.stderr
    assert "would no longer allow you" not in result.stderr


def test_task_formats_agree_on_the_policy_section(phabfive, create_task):
    """Four independent display builders name the section; a Policy added to
    some and not others makes yaml and json disagree about the same task."""
    task_id, _title = create_task()

    def show(*args):
        return phabfive(*args, "maniphest", "show", task_id, "--show-policy")

    from_json = show("--format", "json")
    from_jsonl = show("--format", "jsonl")
    from_yaml = show("--format", "yaml")
    from_rich = show()
    from_tree = show("--format", "tree")

    expected = json.loads(from_json)[0]["Policy"]

    assert expected == json.loads(from_jsonl)["Policy"]
    assert expected == YAML(typ="safe").load(from_yaml)[0]["Policy"]

    for rendered in (from_rich, from_tree):
        for key, value in expected.items():
            assert f"{key}: {value}" in rendered, rendered

    # And every one of them is silent about the section when it was not
    # asked for - the gate is in the record builder, so no renderer can be
    # the one that keeps printing it
    for output_format in ("json", "jsonl", "yaml", "tree"):
        rendered = phabfive("--format", output_format, "maniphest", "show", task_id)

        assert "Policy" not in rendered, rendered

    assert "Policy" not in phabfive("maniphest", "show", task_id)


def test_jsonl_is_one_task_per_line(phabfive, create_task):
    first, first_title = create_task()
    second, second_title = create_task()

    # The fixture puts positional args straight after the interpreter, so a
    # global option passed here still lands before the subcommand
    output = phabfive("--format", "jsonl", "maniphest", "show", first, second)

    lines = output.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["Task"]["Name"] for line in lines] == [
        first_title,
        second_title,
    ]


def test_repo_show_describes_a_seeded_repository(phabfive):
    """GUNNAR is hosted, with history, per phorge/seed/data/repositories.json."""
    [repo] = phabfive(
        "diffusion", "repo", "show", "GUNNAR", "--show-policy", json_output=True
    )

    assert repo["Repository"]["Callsign"] == "GUNNAR"
    assert repo["Repository"]["Short Name"] == "gunnar-firmware"
    assert repo["Repository"]["Default Branch"] == "main"
    assert repo["Repository"]["VCS"] == "git"
    assert repo["Repository"]["Hosted"] is True
    assert repo["Link"].endswith("/source/gunnar-firmware/")
    assert repo["Policy"]["Visible To"] == "All Users"


def test_repo_show_resolves_every_way_in(phabfive):
    """Monogram, callsign and short name all name the same repository.

    The monogram is asked for rather than assumed: the seeder keys on the
    callsign and creates only what is missing, so which R number GUNNAR
    got depends on what was already in the database.
    """
    [seeded] = phabfive("diffusion", "repo", "show", "GUNNAR", json_output=True)
    monogram = seeded["Repository"]["Monogram"]

    monograms = set()
    for identifier in (monogram, "GUNNAR", "gunnar-firmware"):
        [repo] = phabfive("diffusion", "repo", "show", identifier, json_output=True)
        monograms.add(repo["Repository"]["Monogram"])

    assert monograms == {monogram}


def test_repo_show_reads_the_refs_off_a_hosted_repository(phabfive):
    """The reason the seeder builds real history: refs to answer with."""
    [repo] = phabfive(
        "diffusion",
        "repo",
        "show",
        "GUNNAR",
        "--show-branches",
        "--show-tags",
        json_output=True,
    )

    assert repo["Branches"] == ["feature/telemetry", "main"]
    assert repo["Tags"] == ["v1.0.0"]


def test_repo_show_on_a_repository_nobody_pushed_to(phabfive):
    """Empty is an empty result, not a failure."""
    [repo] = phabfive(
        "diffusion",
        "repo",
        "show",
        "SPIKE",
        "--show-branches",
        "--show-tags",
        json_output=True,
    )

    assert repo["Branches"] == []
    assert repo["Tags"] == []


def test_repo_show_takes_several_repositories(phabfive):
    repos = phabfive("diffusion", "repo", "show", "GUNNAR,SPIKE", json_output=True)

    assert [r["Repository"]["Callsign"] for r in repos] == ["GUNNAR", "SPIKE"]


def test_repo_show_formats_agree(phabfive, settled_repositories):
    """The whole point of the command: one record, five ways of writing it.

    `settled_repositories` because this compares four separate runs of the
    CLI: a repository that finishes importing between two of them changes
    `Importing` underneath the comparison.
    """
    from ruamel.yaml import YAML

    load = YAML(typ="safe").load
    args = (
        "diffusion",
        "repo",
        "show",
        "GUNNAR",
        "SPIKE",
        "--show-branches",
        "--show-tags",
    )

    as_json = phabfive(*args, json_output=True)
    as_yaml = load(phabfive("--format", "yaml", *args))
    as_jsonl = [
        json.loads(line) for line in phabfive("--format", "jsonl", *args).splitlines()
    ]
    # Rich is the same record in YAML shape - hyperlinks and colour, not a
    # different layout - and NO_COLOR plus a pipe leave it parseable.
    as_rich = load(phabfive("--format", "rich", *args))

    assert as_yaml == as_json
    assert as_jsonl == as_json
    assert as_rich == as_json


def test_repo_show_on_a_repository_that_does_not_exist(phabfive_raw):
    """A failed lookup, not an empty result."""
    result = phabfive_raw("diffusion", "repo", "show", "R9999")

    assert result.returncode == 1
    assert "not found" in result.stderr


def test_repo_list_lists_the_seeded_repositories(phabfive):
    """`repo list` ignored --format entirely and printed bare names (#372)."""
    repos = phabfive("diffusion", "repo", "list", json_output=True)
    names = [repo["Repository"]["Name"] for repo in repos]

    assert {"GUNNAR", "SPIKE"} <= {repo["Repository"]["Callsign"] for repo in repos}
    assert names == sorted(names)


def test_a_listed_repository_is_the_record_show_answers_with(phabfive):
    """One builder, so a list and a show cannot describe the same thing twice."""
    [shown] = phabfive("diffusion", "repo", "show", "GUNNAR", json_output=True)
    listed = phabfive("diffusion", "repo", "list", json_output=True)

    assert shown in listed


def test_repo_list_formats_agree(phabfive, settled_repositories):
    """Four runs of the CLI again, so the same settling applies."""
    from ruamel.yaml import YAML

    load = YAML(typ="safe").load
    args = ("diffusion", "repo", "list", "all", "--show-uris")

    as_json = phabfive(*args, json_output=True)
    as_jsonl = [
        json.loads(line) for line in phabfive("--format", "jsonl", *args).splitlines()
    ]

    assert load(phabfive("--format", "yaml", *args)) == as_json
    assert as_jsonl == as_json
    assert load(phabfive("--format", "rich", *args)) == as_json


def test_repo_list_table_writes_one_row_per_repository(phabfive):
    """`--format=table` is the list-shaped format (#376)."""
    repos = phabfive("diffusion", "repo", "list", json_output=True)
    rows = phabfive("--format", "table", "diffusion", "repo", "list").splitlines()

    assert len(rows) == len(repos) + 1
    assert rows[0].startswith("Name")
    assert "Monogram" in rows[0]

    for repo in repos:
        assert repo["Repository"]["Monogram"] in "\n".join(rows[1:])


def test_repo_show_falls_back_to_rich_for_table(phabfive):
    """A show has one record and no grid to make of it."""
    output = phabfive("--format", "table", "diffusion", "repo", "show", "GUNNAR")

    assert output.startswith("- Link: ")


def test_repo_edit_sets_every_policy(
    phabfive, phabfive_raw, conduit, create_repository
):
    """The three policy transaction names, against a real Phorge.

    This is the test that could not be written below the CLI. Phorge's edit
    form calls the view and edit fields `policy.view` and `policy.edit`, and
    Conduit refuses both of those spellings in favour of the shorter aliases,
    while the push one goes the other way - `policy.push` is the type and
    `push` is refused. A wrong name is an ERR-CONDUIT-CORE from the instance,
    which a mocked client cannot produce and would happily accept.

    The project and the username are asked for rather than hard-coded: what
    S3 means is positional in the seed file, and a slug is no different.
    """
    repo = create_repository()
    slug = conduit("project.search", limit=1)["data"][0]["fields"]["slug"]
    me = conduit("user.whoami")["userName"]

    phabfive(
        "diffusion",
        "repo",
        "edit",
        repo,
        "--visible-to=public",
        f"--editable-by=#{slug}",
        f"--can-push=@{me}",
        "--yes",
    )

    [record] = phabfive(
        "diffusion", "repo", "show", repo, "--show-policy", json_output=True
    )

    # Read back in the same spelling that set them, so what a policy is
    # shown as can be typed straight back in
    assert record["Policy"] == {
        "Visible To": "Public (No Login Required)",
        "Editable By": f"#{slug}",
        "Can Push": f"@{me}",
    }

    # Asking for what is already there is not a change. The sentence saying
    # so is prose, so under a machine-readable format it is on stderr (#344)
    # and stdout carries the repository's record - "already at the target
    # state" is an answer about the repository, not an absence of one.
    result = phabfive_raw(
        "--format",
        "json",
        "diffusion",
        "repo",
        "edit",
        repo,
        "--visible-to=public",
    )

    assert result.returncode == 0
    assert "No changes" in result.stderr
    assert json.loads(result.stdout)[0]["Repository"]["Short Name"] == repo

    # And Phorge refuses to let the viewer lock themselves out, which is
    # reported as the sentence it answered with
    result = phabfive_raw(
        "diffusion", "repo", "edit", repo, "--visible-to=no-one", "--yes"
    )

    assert result.returncode == 1
    assert "would no longer allow you" in result.stderr
    assert "Traceback" not in result.stderr


def test_repo_list_url_is_a_deprecated_alias(phabfive_raw):
    result = phabfive_raw("--format", "json", "diffusion", "repo", "list", "--url")

    assert result.returncode == 0
    assert "--url is deprecated" in result.stderr
    # The warning is on stderr, so stdout is still parseable JSON - and the
    # alias means what --show-uris means.
    assert all("URIs" in repo for repo in json.loads(result.stdout))


def test_uri_list_on_a_repository_without_uris(phabfive_raw):
    """An empty result, not a failure: the seeded repositories carry no URIs.

    An empty list, so stdout is still a JSON document (#522).
    """
    result = phabfive_raw("--format", "json", "diffusion", "uri", "list", "GUNNAR")

    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_uri_list_refuses_an_io_value_that_is_not_one(phabfive_raw):
    """The filters validate against the constants, before any request (#375)."""
    result = phabfive_raw("diffusion", "uri", "list", "GUNNAR", "--io=bogus")

    assert result.returncode == 1
    assert "not valid" in result.stderr
    assert "'observe'" in result.stderr


def test_uri_list_on_a_repository_that_does_not_exist(phabfive_raw):
    """A traceback before #372; a message and exit 1 now."""
    result = phabfive_raw("--format", "json", "diffusion", "uri", "list", "R9999")

    assert result.returncode == 1
    assert "not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_repo_show_fails_on_a_partial_result(phabfive_raw):
    """GUNNAR is shown, and the exit code still says something was missed."""
    result = phabfive_raw(
        "--format", "jsonl", "diffusion", "repo", "show", "GUNNAR,R9999"
    )

    assert result.returncode == 1
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["Repository"]["Callsign"] == "GUNNAR"


def test_paste_search_and_show_emit_the_same_record(phabfive):
    """Two shapes before #450 - {"id", "title"} and a flat record - one now."""
    title = f"e2e paste {uuid.uuid4().hex[:8]}"
    [created] = phabfive(
        "paste", "create", title, "--content=line one", "--yes", json_output=True
    )
    link = created["Link"]
    monogram = urlparse(link).path.strip("/")

    [shown] = phabfive("paste", "show", monogram, json_output=True)
    found = phabfive("paste", "search", "--author=@me", "--limit=20", json_output=True)

    [listed] = [record for record in found if record["Link"] == link]
    assert shown["Paste"].pop("Content") == "line one"
    assert listed == shown
    assert shown["Paste"]["Name"] == title
    assert list(shown) == ["Link", "Paste", "Space"]


def test_paste_edit_adds_and_removes_subscribers(phabfive, conduit):
    [created] = phabfive(
        "paste",
        "create",
        f"e2e paste {uuid.uuid4().hex[:8]}",
        "--content=line one",
        "--add-subscriber=@viola.larsson",
        "--yes",
        json_output=True,
    )
    monogram = urlparse(created["Link"]).path.strip("/")
    number = int(monogram[1:])

    phabfive(
        "paste",
        "edit",
        monogram,
        "--add-subscriber=@mikael.wallin",
        "--unsubscribe=@viola.larsson",
    )

    assert _subscribers(conduit, "paste.search", number) == {"mikael.wallin"}


# --------------------------------------------------------------------------
# Create specs against a live instance (#483, #482, #485)
# --------------------------------------------------------------------------


def test_one_spec_creates_a_project_and_the_tasks_tagged_into_it(
    phabfive, conduit, tmp_path
):
    """#483's acceptance, and the reason `$local-id` exists at all.

    A task tagged into a project the same document creates needs a PHID
    that does not exist until apply time. The project is created first -
    the ordering is what makes that true whatever order the sections are
    written in - and the task carries the PHID it came back as.

    The project is named with a per-run prefix and left behind on purpose:
    **Conduit cannot delete or archive a project**, so there is nothing to
    clean up with, and `make reset` is the only cleanup this instance has.
    """
    unique = uuid.uuid4().hex[:8]
    spec = tmp_path / "platform.yaml"
    spec.write_text(
        "spec: phorge/v1alpha1\n"
        "kind: create\n"
        "projects:\n"
        f"  - id: platform\n"
        f'    name: "e2e-spec-{unique}"\n'
        f'    slugs: ["e2e-spec-{unique}"]\n'
        "tasks:\n"
        f'  - title: "e2e-spec-{unique} bootstrap"\n'
        '    projects: ["$platform"]\n'
        f'  - title: "e2e-spec-{unique} follow up"\n'
        '    projects: ["$platform"]\n'
    )

    records = phabfive("maniphest", "create", "--with", str(spec), json_output=True)

    # One record per object, **including the project**, which is what
    # unifying the three `create --with` commands on `apply -f`'s runner
    # bought: the old template path answered with `maniphest show` records,
    # so a spec that created a project reported it nowhere.
    assert [one["type"] for one in records] == ["project", "task", "task"]

    projects = conduit(
        "project.search", **{"constraints[slugs][0]": f"e2e-spec-{unique}"}
    )["data"]
    assert len(projects) == 1
    project_phid = projects[0]["phid"]

    assert records[0]["phid"] == project_phid
    assert records[0]["local_id"] == "platform"

    ids = [one["id"] for one in records if one["type"] == "task"]
    found = conduit(
        "maniphest.search",
        **{
            "constraints[ids][0]": ids[0],
            "constraints[ids][1]": ids[1],
            "attachments[projects]": "1",
        },
    )["data"]

    assert len(found) == 2
    for task in found:
        assert task["attachments"]["projects"]["projectPHIDs"] == [project_phid]


def test_anchoring_subtasks_keeps_the_ones_the_task_already_had(
    phabfive, create_task, conduit, tmp_path
):
    """The data-loss trap the whole phase exists to close.

    An anchor names an existing task and is never written: each new child
    carries a `parents.add` naming it, so the subtasks it already had
    cannot be discarded. A `subtasks.set` on the parent - which is what the
    old template path emitted - would replace the list with the two new
    ones.
    """
    epic, _ = create_task()
    existing_a, title_a = create_task()
    existing_b, title_b = create_task()

    def phid_of(monogram):
        [task] = conduit(
            "maniphest.search",
            **{"constraints[ids][0]": int(monogram.lstrip("T"))},
        )["data"]
        return task["phid"]

    conduit(
        "maniphest.edit",
        **{
            "objectIdentifier": epic,
            "transactions[0][type]": "subtasks.add",
            "transactions[0][value][0]": phid_of(existing_a),
            "transactions[0][value][1]": phid_of(existing_b),
        },
    )

    def subtask_titles():
        [task] = phabfive("maniphest", "show", epic, json_output=True)
        return sorted(one["Task"]["Name"] for one in task["Subtasks"])

    before = subtask_titles()
    assert before == sorted([title_a, title_b])

    unique = uuid.uuid4().hex[:8]
    spec = tmp_path / "anchor.yaml"
    spec.write_text(
        "spec: phorge/v1alpha1\n"
        "kind: create\n"
        "tasks:\n"
        f"  - parent: {epic}\n"
        "    tasks:\n"
        f'      - title: "e2e-anchor-{unique} C"\n'
        f'      - title: "e2e-anchor-{unique} D"\n'
    )

    phabfive("maniphest", "create", "--with", str(spec))

    after = subtask_titles()

    assert set(before) <= set(after)
    assert len(after) == 4


def test_a_spec_whose_object_is_refused_reports_what_exists(
    phabfive_raw, conduit, tmp_path
):
    """#485 end to end: created, failed and skipped, and exit status 1.

    A title of more than 255 characters is refused by Maniphest itself, so
    the failure comes from the instance rather than from a mock.
    """
    unique = uuid.uuid4().hex[:8]
    spec = tmp_path / "partial.yaml"
    spec.write_text(
        "spec: phorge/v1alpha1\n"
        "kind: create\n"
        "tasks:\n"
        f'  - title: "e2e-partial-{unique} one"\n'
        f'  - title: "{"x" * 300}"\n'
        f'  - title: "e2e-partial-{unique} three"\n'
    )

    result = phabfive_raw(
        "--format", "jsonl", "maniphest", "create", "--with", str(spec)
    )

    assert result.returncode == 1

    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    assert [one["status"] for one in records] == ["created", "failed", "skipped"]
    assert records[0]["monogram"].startswith("T")
    assert "255" in records[1]["reason"]
    assert "3 of 3" not in result.stderr

    # The one that was created really is there: a report that named an
    # object it had not created would be worse than no report
    found = conduit("maniphest.search", **{"constraints[ids][0]": records[0]["id"]})[
        "data"
    ]
    assert found[0]["fields"]["name"] == f"e2e-partial-{unique} one"


def test_a_create_spec_refuses_passphrases_offline(phabfive_raw, tmp_path):
    """Phorge exposes no `passphrase.edit`, so no token is needed to know."""
    spec = tmp_path / "creds.yaml"
    spec.write_text(
        "spec: phorge/v1alpha1\n"
        "kind: create\n"
        "passphrases:\n"
        '  - name: "Deploy key"\n'
        '    secret: "not-a-real-secret"\n'
    )

    result = phabfive_raw("--format", "json", "spec", "validate", str(spec))

    assert result.returncode == 1

    [problem] = json.loads(result.stdout)

    assert problem["code"] == "not-creatable"
    assert "no passphrase.edit endpoint" in problem["reason"]
    # The section's body is counted, never echoed: it is credential material
    assert problem["value"] == "1 item(s)"
    assert "not-a-real-secret" not in result.stdout


def _imported_commits(conduit, callsign):
    """The commits of one repository, once its import has finished.

    Only this repository is waited for, not every one as
    `settled_repositories` does: a commit row exists only once the daemons
    have discovered it, and an inactive repository elsewhere on the instance
    never finishes importing at all.
    """
    deadline = time.monotonic() + 180

    while True:
        [repo] = conduit(
            "diffusion.repository.search", **{"constraints[callsigns][0]": callsign}
        )["data"]

        if not repo["fields"].get("isImporting"):
            return conduit(
                "diffusion.commit.search",
                **{"constraints[repositories][0]": repo["phid"]},
            )["data"]

        assert time.monotonic() < deadline, f"r{callsign} still importing after 180s"
        time.sleep(2)


def test_commits_attach_and_detach_by_short_hash(
    phabfive, phabfive_raw, conduit, create_task
):
    """`--attach` on create and edit, and `--detach`, against GUNNAR's history.

    A bare short hash is the spelling only the instance can resolve: it is
    looked for in every repository, and what `maniphest show` names it by is
    the monogram Diffusion gives it. Attaching one that is already attached,
    or detaching one that is not, is no change, as for subscribers.
    """
    commits = _imported_commits(conduit, "GUNNAR")
    assert len(commits) >= 2, "the seed gives GUNNAR three commits"
    first, second = (commit["fields"]["identifier"] for commit in commits[:2])

    def attached():
        [task] = phabfive("maniphest", "show", task_id, json_output=True)
        return sorted(c["Commit"]["Identifier"] for c in task["Commits"])

    task_id, _title = create_task("--attach", first[:7])
    phabfive("maniphest", "edit", task_id, "--attach", f"rGUNNAR{second[:12]}")

    assert attached() == sorted([f"rGUNNAR{first[:12]}", f"rGUNNAR{second[:12]}"])

    [task] = phabfive("maniphest", "show", task_id, json_output=True)
    assert all(c["Link"] and c["Commit"]["Summary"] for c in task["Commits"])

    again = phabfive_raw("maniphest", "edit", task_id, "--attach", first, "--yes")
    assert again.returncode == 0, again.stderr
    assert "No changes" in again.stderr

    phabfive("maniphest", "edit", task_id, "--detach", first[:7], "--yes")
    assert attached() == [f"rGUNNAR{second[:12]}"]

    gone = phabfive_raw("maniphest", "edit", task_id, "--detach", first, "--yes")
    assert gone.returncode == 0, gone.stderr
    assert "No changes" in gone.stderr

    unknown = phabfive_raw("maniphest", "edit", task_id, "--attach", "0" * 12)
    assert unknown.returncode == 1
    assert "No such commit" in unknown.stderr


# --------------------------------------------------------------------------
# `phabfive apply -f` and `phabfive search -f` (#486)
# --------------------------------------------------------------------------


def test_apply_creates_a_project_and_the_tasks_tagged_into_it(
    phabfive, phabfive_raw, conduit, tmp_path
):
    """#486's acceptance: the verb runs the whole document.

    The same spec as `maniphest create --with` runs above, through the
    top-level command instead - which is the point of having one, since a
    document creating a project does not belong under `maniphest`. The dry
    run goes first and has to leave the instance untouched, because a
    preview that writes is the one thing `--dry-run` may never do.

    The project is left behind on purpose: **Conduit cannot delete or
    archive a project**, so `make reset` is this instance's only cleanup.
    """
    unique = uuid.uuid4().hex[:8]
    spec = tmp_path / "bootstrap.yaml"
    spec.write_text(
        "spec: phorge/v1alpha1\n"
        "kind: create\n"
        "metadata:\n"
        "  description: A project and the tasks tagged into it\n"
        "projects:\n"
        "  - id: platform\n"
        f'    name: "e2e-apply-{unique}"\n'
        f'    slugs: ["e2e-apply-{unique}"]\n'
        "tasks:\n"
        "  - id: epic\n"
        f'    title: "e2e-apply-{unique} epic"\n'
        '    projects: ["$platform"]\n'
        f'  - title: "e2e-apply-{unique} follow up"\n'
        '    projects: ["$platform"]\n'
        '    parents: ["$epic"]\n'
    )

    dry = phabfive_raw("--format", "json", "apply", "-f", str(spec), "--dry-run")

    assert dry.returncode == 0, f"{dry.stdout}\n{dry.stderr}"
    assert [one["type"] for one in json.loads(dry.stdout)] == [
        "project",
        "task",
        "task",
    ]
    assert (
        conduit("project.search", **{"constraints[slugs][0]": f"e2e-apply-{unique}"})[
            "data"
        ]
        == []
    )

    records = phabfive("apply", "-f", str(spec), json_output=True)

    assert [one["status"] for one in records] == ["created", "created", "created"]
    assert [one["type"] for one in records] == ["project", "task", "task"]

    projects = conduit(
        "project.search", **{"constraints[slugs][0]": f"e2e-apply-{unique}"}
    )["data"]
    assert len(projects) == 1
    project_phid = projects[0]["phid"]

    tagged = conduit(
        "maniphest.search",
        **{
            "constraints[ids][0]": records[1]["id"],
            "constraints[ids][1]": records[2]["id"],
            "attachments[projects]": "1",
        },
    )["data"]

    assert len(tagged) == 2
    for task in tagged:
        assert task["attachments"]["projects"]["projectPHIDs"] == [project_phid]

    # The second task hangs off the first, by the `$epic` the spec named
    [child] = [one for one in tagged if one["id"] == records[2]["id"]]
    parents = conduit(
        "maniphest.search",
        **{"constraints[subtaskIDs][0]": child["id"]},
    )["data"]
    assert [one["id"] for one in parents] == [records[1]["id"]]


def test_each_command_refuses_the_other_kind_by_name(phabfive_raw, tmp_path):
    """Neither is a schema error: each names the command that does run it."""
    create = tmp_path / "create.yaml"
    create.write_text('kind: create\ntasks:\n  - title: "nothing is created"\n')

    search = tmp_path / "search.yaml"
    search.write_text("kind: search\nsearches:\n  - search: {limit: 1}\n")

    refused_apply = phabfive_raw("apply", "-f", str(search))
    refused_search = phabfive_raw("search", "-f", str(create))

    assert refused_apply.returncode == 1
    assert f"phabfive search -f {search}" in refused_apply.stderr
    assert refused_search.returncode == 1
    assert f"phabfive apply -f {create}" in refused_search.stderr

    # Nothing was created by the refusal, which is what "run it with" means
    assert refused_search.stdout.strip() == ""


def test_search_runs_every_object_type_one_spec_holds(phabfive_raw, tmp_path):
    """One file, a task search and a project search, in document order."""
    spec = tmp_path / "readiness.yaml"
    spec.write_text(
        "spec: phorge/v1alpha1\n"
        "kind: search\n"
        "searches:\n"
        "  - type: task\n"
        "    search: {status: open, limit: 3}\n"
        "  - type: project\n"
        "    search: {status: active, limit: 3}\n"
    )

    result = phabfive_raw("--format", "jsonl", "search", "-f", str(spec))

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    sections = [next(key for key in one if key != "Link") for one in records]

    # Not an exact list: what the instance holds is not this test's to say.
    # Both types ran, each record says which it is, and tasks came first.
    assert set(sections) == {"Task", "Project"}
    assert sections == sorted(sections, key=["Task", "Project"].index)
