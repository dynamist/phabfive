# -*- coding: utf-8 -*-
"""Every spec shipped in the repository loads, validates and says what it is.

The docs point users at the files under `specs/`, and for a long time
nothing loaded any of them: the suite builds its fixtures inline with
`tmp_path`. That is how the beginner example the create guide told a new
user to copy kept a `tickets:` root key long after the code started
requiring `tasks:` - the one file a new user was handed was the one file
that could not load.

This is the gate that would have caught it, and it is the same gate
`phabfive spec validate --offline` is: every file in the corpus goes through
the production loader and the offline validation pass, and a file that fails
fails *by name* rather than as one opaque walk. `test_the_command_validates_
the_whole_corpus` runs the real command in a subprocess with no token, no
`PHAB_URL` and a `HOME` that does not exist, which is the promise the offline
layer is built on: a CI job or a pre-commit hook can lint a repository of
specs on a machine that was never configured.

The corpus is found by globbing `specs/`, so a file added to it is gated by
being added. The kind of each file is read from the directory it sits in, so
a create spec filed under `specs/search/` is a failure rather than a silent
reinterpretation.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from phabfive.spec import Kind, load_spec, validate_offline
from phabfive.spec.envelope import SPEC_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The one directory that holds shipped specs, renamed from the old template
#: directory by #491. A single root is named here rather than globbed for, so
#: a corpus that moved again fails loudly in `test_specs_are_shipped`
#: instead of walking nothing and passing every case below.
CORPUS_DIR_NAME = "specs"

CORPUS_ROOT = REPO_ROOT / CORPUS_DIR_NAME

#: Every serialization `phabfive.spec.loader` reads. Not only YAML, so a
#: format-parity example added in JSON, JSONL or TOML is gated too.
SPEC_SUFFIXES = {".yaml", ".yml", ".json", ".jsonl", ".toml"}

#: A directory of specs that are wrong on purpose, each one the fixture for
#: the problem code it produces. It is excluded here rather than skipped
#: inside each case: a walk that asserts "everything validates" cannot also
#: hold the files that must not, and the exclusion belongs in the glob where
#: it is read once.
BROKEN_DIR_NAME = "broken"

#: What a create spec may hold. A spec that creates one project and no task
#: is a create spec, so the walk asks for items of *some* type rather than
#: for tasks. `passphrases` is left out on purpose: it is a key the loader
#: recognises in order to refuse it, and a shipped example may never hold one.
CREATE_OBJECT_TYPES = ("task", "project", "paste")

#: Which kind a directory name declares, by the word in it. Matched as a
#: word *in* the directory name rather than as the whole of it, so a corpus
#: renamed again - to `create-specs/`, or back to a compound name - keeps
#: declaring the same thing without touching this file.
KIND_WORDS = (("create", Kind.CREATE), ("search", Kind.SEARCH))


def _shipped_specs():
    """Every spec file under every corpus root, sorted for stable ids."""
    return sorted(
        path
        for path in CORPUS_ROOT.rglob("*")
        if path.is_file()
        and path.suffix in SPEC_SUFFIXES
        and BROKEN_DIR_NAME not in _directories(path)
    )


def _directories(path):
    """The directory names between the repository root and one file.

    Measured from the repository rather than from the corpus root, so a file
    carries the name of the root it is in. `specs` holds none of the words in
    `KIND_WORDS`, so naming it costs nothing.
    """
    return [part.lower() for part in path.relative_to(REPO_ROOT).parts[:-1]]


def _spec_id(path):
    """The path relative to the repository, as the test's id."""
    return str(path.relative_to(REPO_ROOT))


SPECS = _shipped_specs()


def _declared_kind(path):
    """The kind the directory a file sits in names, or None if none does."""
    for directory in _directories(path):
        for word, kind in KIND_WORDS:
            if word in directory:
                return kind

    return None


CREATE_SPECS = [path for path in SPECS if _declared_kind(path) is Kind.CREATE]
SEARCH_SPECS = [path for path in SPECS if _declared_kind(path) is Kind.SEARCH]

# A directory that names no kind is legal and is not checked for one: the
# format-parity examples sit together because they are the same spec in four
# serializations, and half of them are creates and half searches. They are
# still loaded and validated like everything else.
FILED_SPECS = CREATE_SPECS + SEARCH_SPECS


def _walk_items(items):
    """Every item in a create section, nested subtasks included.

    `subtasks:` nest, and a nested one carries `visible-to:` like any other,
    so a walk that looked only at the top level would miss exactly the case
    `platform-bootstrap.yaml` failed on.
    """
    for item in items or ():
        if not isinstance(item, dict):
            continue

        yield item
        yield from _walk_items(item.get("subtasks"))
        yield from _walk_items(item.get("tasks"))


def _report(path, problems):
    """Every problem in one file, one per line, named for a human."""
    lines = [
        f"  {problem.severity}: {problem.object}"
        + (f".{problem.field}" if problem.field else "")
        + f": {problem.reason} [{problem.code}]"
        for problem in problems
    ]

    return "\n".join([f"{_spec_id(path)} does not validate offline:", *lines])


def _check_task_list(tasks, where):
    """A create spec's `tasks`, and the `tasks` of each task under it.

    Every task has a title, with one exception the format defines: an
    **anchor**, which is an item carrying `parent:` and nested `tasks:` and
    no title of its own. It creates nothing - it names the existing task its
    children hang off - so requiring a title of it would forbid the one shape
    that puts new subtasks under an existing one.
    """
    assert isinstance(tasks, list), (
        f"{where} must be a list, not {type(tasks).__name__}"
    )
    assert tasks, f"{where} is empty"

    for index, task in enumerate(tasks):
        at = f"{where}[{index}]"

        assert isinstance(task, dict), f"{at} must be a mapping"

        title = task.get("title")
        children = task.get("tasks")

        if title is None and task.get("parent") is not None:
            assert children, f"{at} anchors on {task['parent']} but creates nothing"
        else:
            assert isinstance(title, str) and title.strip(), f"{at} has no title"

        if children is not None:
            _check_task_list(children, f"{at}.tasks")


def scrubbed_environment():
    """As little environment as an interpreter needs to start.

    No `PHAB_URL`, no `PHAB_TOKEN`, and a `HOME` that does not exist, so
    nothing can read `~/.arcrc`, `~/.config/phabfive.yaml` or a cache
    directory. `SYSTEMROOT` is what Windows needs to start Python at all, and
    `PATH` is kept because `uv run` put the interpreter on it.

    Copied from `tests/test_spec_isolation.py` rather than imported: both
    files pin the same promise from opposite ends, and a shared helper is one
    more thing a future move has to keep in step.
    """
    keep = ("PATH", "SYSTEMROOT", "LD_LIBRARY_PATH", "VIRTUAL_ENV")
    environment = {name: os.environ[name] for name in keep if name in os.environ}

    nowhere = str(Path(os.sep, "nonexistent"))

    environment["HOME"] = nowhere
    # Windows names the places a configuration could come from with its own
    # variables, and they are pointed at the same nowhere rather than left
    # out: `phabricator/__init__.py` reads `os.environ['ProgramData']` and
    # `os.environ['AppData']` as it imports, so an *absent* one is a KeyError
    # before anything is validated, not an empty directory.
    environment["USERPROFILE"] = nowhere
    environment["APPDATA"] = nowhere
    environment["LOCALAPPDATA"] = nowhere
    environment["ProgramData"] = nowhere
    environment["ALLUSERSPROFILE"] = nowhere
    environment["PYTHONPATH"] = str(REPO_ROOT)
    environment["TERM"] = "dumb"

    assert "PHAB_URL" not in environment
    assert "PHAB_TOKEN" not in environment

    return environment


# One child process for the whole corpus rather than one per file: what is
# under test is that the command needs nothing from the machine, and that is
# the same fact eleven times. The child reports per file, so a failure still
# names the file and shows what the command printed about it.
VALIDATE_EVERY_FILE = """\
import json
import sys

from click.testing import CliRunner
from typer.main import get_command

from phabfive.cli import app

command = get_command(app)
runner = CliRunner()

failures = []

for path in json.loads(sys.argv[1]):
    result = runner.invoke(command, ["spec", "validate", path, "--offline"])

    if result.exit_code != 0:
        failures.append([path, result.exit_code, result.output])

print(json.dumps(failures))
"""


def test_specs_are_shipped():
    """Guards every test below: an empty glob would pass them all."""
    assert CORPUS_ROOT.is_dir(), f"No {CORPUS_DIR_NAME}/ directory in {REPO_ROOT}"

    where = CORPUS_DIR_NAME

    assert SPECS, f"No spec files found under {where}"
    assert CREATE_SPECS, f"No create specs found under {where}"
    assert SEARCH_SPECS, f"No search specs found under {where}"


@pytest.mark.parametrize("path", SPECS, ids=_spec_id)
def test_a_shipped_spec_loads(path):
    """The production loader reads the file, with no `kind=` to help it."""
    spec = load_spec(path)

    assert spec.body, "spec loaded to an empty body"


@pytest.mark.parametrize("path", SPECS, ids=_spec_id)
def test_a_shipped_spec_validates_offline(path):
    """Layer 1 over the whole corpus: no network, no token, no problems.

    This is the case that would have caught the old beginner example, whose
    root key the code stopped reading long before anyone noticed.
    """
    problems = validate_offline(load_spec(path))

    assert problems == [], _report(path, problems)


@pytest.mark.parametrize("path", SPECS, ids=_spec_id)
def test_a_shipped_spec_declares_its_envelope(path):
    """The shipped files are what a user copies, so they show the format.

    Inference would read every one of them anyway - that is the ergonomics
    the loader keeps - but an example that leans on it teaches a shape with
    no version in it, and `phorge/v1alpha1` is free to churn.
    """
    spec = load_spec(path)
    where = _spec_id(path)

    assert spec.envelope.spec == SPEC_VERSION, (
        f"{where} declares {spec.envelope.spec!r}, not {SPEC_VERSION!r}"
    )
    assert spec.envelope.kind_declared, f"{where} leaves its kind to inference"

    metadata = spec.envelope.metadata

    assert metadata.description, f"{where} has no metadata.description"
    assert metadata.version, f"{where} has no metadata.version"
    assert metadata.author, f"{where} has no metadata.author"
    assert not metadata.extra, (
        f"{where} carries metadata keys nothing reads: {sorted(metadata.extra)}"
    )


@pytest.mark.parametrize("path", FILED_SPECS, ids=_spec_id)
def test_a_shipped_spec_is_the_kind_its_directory_says(path):
    """A create spec filed under the search directory is a mistake.

    It is not a harmless one: `load_spec(path, kind=...)` reinterprets rather
    than refuses, so a misfiled file becomes an unconstrained search the day
    something loads the directory with a kind.
    """
    spec = load_spec(path)
    expected = _declared_kind(path)

    assert spec.kind is expected, (
        f"{_spec_id(path)} is in a {expected.value} directory but declares "
        f"{spec.kind.value}"
    )


@pytest.mark.parametrize("path", CREATE_SPECS, ids=_spec_id)
def test_a_create_spec_has_something_to_create(path):
    """Checked structurally: resolving the names needs an instance.

    `validate_offline` already covers the keys and their shapes; what this
    adds is that the document actually holds items, so an example that
    renders to nothing cannot pass the walk above by being empty.

    A create spec need not hold tasks - a spec that creates one project and
    nothing else is a create spec - so what is required is items of *some*
    type, and the task tree is walked only when there is one.
    """
    spec = load_spec(path)

    items = {
        object_type: spec.items(object_type)
        for object_type in CREATE_OBJECT_TYPES
        if spec.items(object_type)
    }

    assert items, (
        f"{_spec_id(path)} creates nothing: it holds none of "
        f"{', '.join(CREATE_OBJECT_TYPES)}"
    )

    if "task" in items:
        _check_task_list(items["task"], "tasks")


@pytest.mark.parametrize("path", SEARCH_SPECS, ids=_spec_id)
def test_a_search_spec_has_criteria(path):
    """Every item names what it searches and has something to search by.

    An item with no `search:` at all is legal - a banner with no criteria -
    but a *shipped* one would run the unconstrained search that returns the
    whole instance, which is never what an example means.
    """
    items = load_spec(path).items("search")

    assert items, "search spec holds no searches"

    for index, item in enumerate(items):
        at = f"searches[{index}]"
        criteria = item.get("search")

        assert isinstance(criteria, dict) and criteria, f"{at} has no criteria"


def test_the_command_validates_the_whole_corpus():
    """`phabfive spec validate --offline`, on a machine with nothing on it.

    The in-process cases above import the validation pass directly. This one
    runs the command a user or a CI job runs, in a child interpreter with no
    `PHAB_TOKEN`, no `PHAB_URL` and a `HOME` that does not exist, so a file
    that only validates because the developer's machine is configured fails
    here.
    """
    paths = [str(path) for path in SPECS]

    finished = subprocess.run(
        [sys.executable, "-c", VALIDATE_EVERY_FILE, json.dumps(paths)],
        env=scrubbed_environment(),
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert finished.returncode == 0, (
        f"the child never reported:\n{finished.stdout}\n{finished.stderr}"
    )

    failures = json.loads(finished.stdout.strip().splitlines()[-1])

    assert failures == [], "\n".join(
        f"{path}: exit {code}\n{output}" for path, code, output in failures
    )


@pytest.mark.parametrize("path", CREATE_SPECS, ids=_spec_id)
def test_a_spec_is_visible_to_whoever_runs_it(path):
    """A shipped example may not lock its creator out of what it creates.

    Phorge refuses to write an object whose view policy would hide it from
    the person writing it: `ERR-CONDUIT-CORE: The view policy of this object
    would no longer allow you to view the object.` So an item that is
    `visible-to: "$some-project"` can only be created by a member of that
    project - and when the project is one the same document creates, the
    members it lists are the whole of that.

    `specs/create/platform-bootstrap.yaml` shipped without `@me` among them.
    It validated offline, it validated online, and `apply --dry-run` planned
    it cleanly, because a policy is only checked when something is actually
    written; a real `apply` created the two projects and was refused on the
    first task, leaving the run at exit 4 with half a bootstrap on the
    instance. Neither validation layer can see this and neither can the
    corpus dry-run gate in `tests/e2e/test_spec_corpus_online.py`, which is
    dry-run by design - a real apply of a shipped example cannot be repeated,
    because a hashtag is taken once.

    So it is checked here, structurally and offline: if anything in the
    document is visible to a project the document itself creates, that
    project has to list `@me`.
    """
    spec = load_spec(path)

    created = {
        item["id"]: item
        for object_type in CREATE_OBJECT_TYPES
        for item in _walk_items(spec.items(object_type))
        if item.get("id")
    }

    guarded = sorted(
        {
            policy.lstrip("$")
            for object_type in CREATE_OBJECT_TYPES
            for item in _walk_items(spec.items(object_type))
            if isinstance(policy := item.get("visible-to"), str)
            and policy.startswith("$")
            and policy.lstrip("$") in created
        }
    )

    for local_id in guarded:
        members = created[local_id].get("members") or []

        assert "@me" in members, (
            f'{_spec_id(path)}: something is `visible-to: "${local_id}"`, '
            f"and ${local_id} does not list `@me` among its members. Phorge "
            f"refuses to create an object the creator could not then see, so "
            f"a real `apply` of this file stops partway."
        )
