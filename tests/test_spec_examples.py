# -*- coding: utf-8 -*-

"""The examples added for the object types the corpus did not cover (#490).

`tests/test_spec_corpus.py` walks every shipped spec and asserts that it
loads and validates. That walk cannot notice an example that has stopped
demonstrating the thing it exists for: a `platform-bootstrap.yaml` whose
`$local` references were edited away still loads, still validates, and is
still the only file in the tree showing what `$` means.

So each example is named here, and each is checked for the one property it
was written to carry. A file renamed or deleted fails by name rather than
vanishing from a glob.

Everything here is offline. `unknown-user.yaml` and the rest of the broken
set are `tests/test_spec_broken_corpus.py`'s, and the four-format fixtures
are `tests/test_spec_format_parity.py`'s.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from phabfive.maniphest.core import Maniphest
from phabfive.spec import Kind, Severity, load_spec, validate_offline
from phabfive.spec.create import plan_create
from phabfive.spec.online import Resolution
from phabfive.spec.references import (
    RefKind,
    declared_local_ids,
    iter_references,
    local_id,
)

REPOSITORY = Path(__file__).resolve().parent.parent
SPECS = REPOSITORY / "specs"

#: The create examples this issue added, and the kind each one must load as.
CREATE_EXAMPLES = (
    "platform-project.yaml",
    "release-notes-paste.yaml",
    "platform-bootstrap.yaml",
    "anchor-existing-task.yaml",
)

#: The search examples, one per object type plus the mixed document.
SEARCH_EXAMPLES = (
    "active-projects.yaml",
    "my-pastes.yaml",
    "deploy-credentials.yaml",
    "release-readiness.yaml",
)

EXAMPLES = tuple(
    [("create", name) for name in CREATE_EXAMPLES]
    + [("search", name) for name in SEARCH_EXAMPLES]
)


def _path(directory, name):
    return SPECS / directory / name


def _spec(directory, name):
    return load_spec(_path(directory, name))


def _errors(spec):
    return [
        problem
        for problem in validate_offline(spec)
        if problem.severity == Severity.ERROR
    ]


def _items(spec, key):
    """One body key's items, or an empty list when the spec has none."""
    return list(spec.body.get(key, []))


def _app():
    """An already-constructed app, which is all planning ever takes.

    `Phabfive.__init__` is faked and `.phab` is a `MagicMock`, the way
    `tests/test_create_port_parity.py` does it: no configuration is read and
    no socket is opened.
    """
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        app = Maniphest()

    app.phab = MagicMock()
    app.url = "http://phorge.example.com"
    app.conf = {}
    app.lookup_store = None
    return app


def _plan(name):
    """What the create example would create, with nothing resolved.

    An empty `Resolution()` is how a caller asks for the *shape* of a
    document: `plan_create` uses a resolution as given and never adds to
    it, so every reference comes back unanswered and no instance is asked
    about anything. What is under test is that the planner walks the
    document at all - a section it refuses is refused here too, offline,
    rather than on whatever machine first runs the example.
    """
    spec = _spec("create", name).render()

    return plan_create(_app(), spec, validate=False, resolution=Resolution())


# --------------------------------------------------------------------------
# Every example, whatever it demonstrates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "directory, name", EXAMPLES, ids=[f"{d}/{n}" for d, n in EXAMPLES]
)
def test_the_example_is_shipped(directory, name):
    """Guards everything below: a missing file must fail by name."""
    assert _path(directory, name).is_file(), (
        f"specs/{directory}/{name} is named by tests/test_spec_examples.py "
        "and by the guides, and is not in the tree"
    )


@pytest.mark.parametrize(
    "directory, name", EXAMPLES, ids=[f"{d}/{n}" for d, n in EXAMPLES]
)
def test_the_example_validates_offline(directory, name):
    """No token, no network, no errors."""
    problems = _errors(_spec(directory, name))

    assert not problems, "\n".join(
        f"{problem.code}: {problem.object}.{problem.field}: {problem.reason}"
        for problem in problems
    )


@pytest.mark.parametrize(
    "directory, name", EXAMPLES, ids=[f"{d}/{n}" for d, n in EXAMPLES]
)
def test_the_example_declares_its_envelope(directory, name):
    """An example is what someone copies, so it says what it is.

    JSON, JSONL and TOML carry no comments, which is why a description
    belongs in `metadata:` and not only in a comment at the top.
    """
    spec = _spec(directory, name)

    assert spec.envelope.spec == "phorge/v1alpha1"
    assert spec.envelope.kind is Kind[directory.upper()]
    assert spec.envelope.kind_declared, (
        "an example declares kind: rather than relying on inference"
    )
    assert spec.envelope.metadata.description


# --------------------------------------------------------------------------
# What each create example is for
# --------------------------------------------------------------------------


def test_platform_project_creates_one_project_and_nothing_else():
    """The project example: the smallest create spec that creates no task."""
    spec = _spec("create", "platform-project.yaml")

    assert not _items(spec, "tasks")
    assert not _items(spec, "pastes")

    projects = _items(spec, "projects")

    assert len(projects) == 1

    project = projects[0]

    assert project["name"]
    assert project["slugs"], "the example shows additional hashtags"
    assert project["members"], "the example shows members"
    assert {"visible-to", "editable-by", "joinable-by"} <= set(project), (
        "the example shows all three of a project's policies"
    )


def test_release_notes_paste_creates_a_paste_with_content():
    """The paste example: `title:` and `content:`, never `name:`/`body:`."""
    spec = _spec("create", "release-notes-paste.yaml")

    pastes = _items(spec, "pastes")

    assert len(pastes) == 1

    paste = pastes[0]

    assert paste["title"]
    assert paste["content"]
    assert paste["language"], "the example shows the highlighting key"
    assert paste["projects"] and paste["subscribers"]


@pytest.mark.parametrize(
    "name, objects",
    [
        ("platform-project.yaml", 1),
        ("platform-bootstrap.yaml", 5),
        ("anchor-existing-task.yaml", 4),
        ("release-notes-paste.yaml", 1),
    ],
)
def test_the_create_example_plans(name, objects):
    """An example the planner refuses is an example nobody can run.

    Counted rather than merely planned, because a document silently
    dropping half of itself plans perfectly well: the anchor's four items
    include the anchor, which creates nothing and is the one item whose
    presence says the nesting was walked.

    `release-notes-paste.yaml` is deliberately not in this list - see
    `test_the_paste_example_is_the_one_the_planner_cannot_plan_yet`.
    """
    plan = _plan(name)

    assert len(plan.items) == objects
    assert all(item.display.get("title") for item in plan.creating)


def test_the_paste_example_plans_a_paste_and_nothing_else():
    """The second half of #481, pinned where the gap used to be.

    `specs/create/release-notes-paste.yaml` was shipped while nothing
    created a paste, and a test asserted the refusal so that the gap could
    not be forgotten. The builder landed, so the same file now asserts the
    opposite: one paste item, built from the keys the registry declares for
    (paste, create), and no other object type smuggled in.

    Planned with an empty `Resolution()`, which is how this module walks a
    spec with no instance to resolve against - so `projects:` and
    `subscribers:` are dropped and what is left is the paste's own shape.
    """
    from phabfive.spec.create import CREATABLE_TYPES

    assert "paste" in CREATABLE_TYPES

    plan = _plan("release-notes-paste.yaml")

    assert [item.object_type for item in plan.creating] == ["paste"]
    assert plan.counts() == {"paste": 1}

    sent = {one["type"] for one in plan.items[0].transactions}

    assert {"title", "text", "language"} <= sent
    assert not any(kind.endswith((".set", ".remove")) for kind in sent)


def test_platform_bootstrap_mixes_object_types_and_resolves_every_local_id():
    """The mixed example: two object types, a milestone, and `$refs`.

    Every `$ref` in it names an id the same file declares - which
    `validate_offline` already checks, and which is asserted here so that
    an edit that dropped the references still fails: a spec with no `$ref`
    left in it validates perfectly and demonstrates nothing.
    """
    spec = _spec("create", "platform-bootstrap.yaml")

    projects = _items(spec, "projects")
    tasks = _items(spec, "tasks")

    assert projects and tasks, "the point of this file is that it holds both"

    declared = {value for _, value in declared_local_ids(spec)}

    assert declared, "the example declares local ids"

    used = {
        local_id(reference.value)
        for reference in iter_references(spec)
        if reference.kind is RefKind.LOCAL
    }

    assert used, "the example uses $refs"
    assert used <= declared, f"{sorted(used - declared)} is declared by nothing"

    milestones = [project for project in projects if "milestone-of" in project]

    assert len(milestones) == 1, "the example shows a milestone"
    assert milestones[0]["milestone-of"].startswith("$"), (
        "the milestone hangs off the project this same file creates"
    )


def test_anchor_existing_task_anchors_rather_than_creating_its_parent():
    """The anchor example: an item with no title creates nothing.

    Both spellings are in the file - the nested one under an anchor, and
    the flat one carrying `parents:` itself - because they are the same
    relationship and people write both.
    """
    spec = _spec("create", "anchor-existing-task.yaml")

    tasks = _items(spec, "tasks")
    anchors = [task for task in tasks if "title" not in task]

    assert len(anchors) == 1, "the example shows exactly one anchor"

    anchor = anchors[0]

    assert anchor["parent"] == "T1", "the anchor names an existing task"
    assert len(anchor["tasks"]) >= 2, "several children share the one anchor"
    assert all("title" in child for child in anchor["tasks"])

    flat = [task for task in tasks if "parents" in task]

    assert flat, "the example also shows the flat spelling"
    assert all(parent == "T1" for task in flat for parent in task["parents"])


# --------------------------------------------------------------------------
# What each search example is for
# --------------------------------------------------------------------------


def _searched(spec):
    """What each `searches:` item searches, in file order."""
    return [item.get("type") for item in spec.items("search")]


def test_active_projects_searches_projects():
    spec = _spec("search", "active-projects.yaml")

    assert _searched(spec) == ["project"]

    search = spec.items("search")[0]["search"]

    assert search["status"] == "active"
    assert search["milestones"] is False, (
        "a milestone is a project too, and would crowd out the real ones"
    )


def test_my_pastes_searches_pastes_as_the_caller():
    spec = _spec("search", "my-pastes.yaml")

    assert _searched(spec) == ["paste"]
    assert spec.items("search")[0]["search"]["author"] == "@me"


def test_deploy_credentials_searches_passphrases_with_a_small_limit():
    """The passphrase example, and the cost it has to state.

    `passphrase.query` publishes no constraints, so every filter is applied
    in phabfive after the page is read. A large limit in a shipped example
    would be a walk of every credential the token can see, run by whoever
    copied the file.
    """
    spec = _spec("search", "deploy-credentials.yaml")

    assert _searched(spec) == ["passphrase"]

    search = spec.items("search")[0]["search"]
    limit = search["limit"]

    assert isinstance(limit, int) and 0 < limit <= 25, (
        f"limit is {limit!r}; a shipped passphrase example keeps it small, "
        "and 0 means every credential"
    )
    assert "show-secret" not in search, (
        "a spec never asks for a secret; the command refuses --show-secret "
        "for a spec-driven search"
    )


def test_release_readiness_mixes_three_applications_in_one_document():
    """The mixed search: `kind:` is the verb, `type:` is the object."""
    spec = _spec("search", "release-readiness.yaml")

    types = _searched(spec)

    assert set(types) == {"task", "project", "paste"}
    assert len(types) == len(spec.items("search"))
    assert all(item.get("title") for item in spec.items("search")), (
        "each item carries its own result banner"
    )
