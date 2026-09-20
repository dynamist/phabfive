# -*- coding: utf-8 -*-
"""The phabfive CLI against a live Phorge, see docs/phorge-setup.md."""

# python std lib
import json
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

    # Asking for what is already there is not a change
    assert "No changes" in phabfive(
        "maniphest", "edit", task_id, f"--visible-to=#{slug}", "--yes"
    )

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

    # Asking for what is already there is not a change
    assert "No changes" in phabfive(
        "diffusion", "repo", "edit", repo, "--visible-to=public"
    )

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
    """An empty result, not a failure: the seeded repositories carry no URIs."""
    result = phabfive_raw("--format", "json", "diffusion", "uri", "list", "GUNNAR")

    assert result.returncode == 0
    assert result.stdout == ""


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
