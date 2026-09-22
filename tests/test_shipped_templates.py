# -*- coding: utf-8 -*-
"""Every template shipped in the repository parses and has a usable shape.

The docs point users at the files under `templates/`, and nothing loaded any
of them: the suite builds its template fixtures inline with `tmp_path`. That
is how `templates/task-create/test-template.yaml` - the beginner example in
docs/create-templates.md - kept a `tickets:` root key long after the code
started requiring `tasks:`, so the one file a new user was told to copy was
the one file that could not load.

The corpus is found by globbing a directory named by a module-level constant,
so the gate survives the directory being renamed, and each file is its own
parametrised case, so a failure names the file rather than the walk.
"""

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from phabfive.maniphest import Maniphest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The directory holding the shipped templates, first of these that exists.
TEMPLATE_DIR_NAMES = ("templates", "specs")

TEMPLATE_ROOT = next(
    (REPO_ROOT / name for name in TEMPLATE_DIR_NAMES if (REPO_ROOT / name).is_dir()),
    REPO_ROOT / TEMPLATE_DIR_NAMES[0],
)

YAML_SUFFIXES = {".yaml", ".yml"}

#: Everything a creation template may say at the root of the document
CREATE_ROOT_KEYS = {"variables", "tasks"}


def _shipped_templates():
    """Every YAML file under the template directory, sorted for stable ids."""
    return sorted(
        path
        for path in TEMPLATE_ROOT.rglob("*")
        if path.is_file() and path.suffix in YAML_SUFFIXES
    )


def _template_id(path):
    """The path relative to the template directory, as the test's id."""
    return str(path.relative_to(TEMPLATE_ROOT))


TEMPLATES = _shipped_templates()


def _load_documents(path):
    """Every YAML document in one template file, as plain data."""
    with open(path, "r", encoding="utf-8") as stream:
        return list(YAML().load_all(stream))


def _template_kind(path, documents):
    """Which shape a template is expected to have, or None if nothing says.

    The directory a template sits in names its kind; a corpus flat enough not
    to say falls back to the root keys of its first document.
    """
    parts = [part.lower() for part in path.relative_to(TEMPLATE_ROOT).parts[:-1]]

    if any("create" in part for part in parts):
        return "create"

    if any("search" in part for part in parts):
        return "search"

    if documents and isinstance(documents[0], dict):
        if "tasks" in documents[0]:
            return "create"

        if "search" in documents[0]:
            return "search"

    return None


def _kind_of(path):
    """The kind of one template, for splitting the corpus at collection time.

    A file that does not parse has no kind; `test_shipped_template_parses` is
    what reports it, rather than collection failing for the whole module.
    """
    try:
        documents = _load_documents(path)
    except Exception:
        documents = []

    return _template_kind(path, documents)


SEARCH_TEMPLATES = [path for path in TEMPLATES if _kind_of(path) == "search"]


def _check_task_list(tasks, where):
    """A creation template's `tasks`, and the `tasks` of each task under it."""
    assert isinstance(tasks, list), (
        f"{where} must be a list, not {type(tasks).__name__}"
    )
    assert tasks, f"{where} is empty"

    for index, task in enumerate(tasks):
        at = f"{where}[{index}]"

        assert isinstance(task, dict), f"{at} must be a mapping"

        title = task.get("title")

        assert isinstance(title, str) and title.strip(), f"{at} has no title"

        children = task.get("tasks")

        if children is not None:
            _check_task_list(children, f"{at}.tasks")


def test_templates_are_shipped():
    """Guards every test below: an empty glob would pass them all."""
    assert TEMPLATE_ROOT.is_dir(), (
        f"No template directory found in {REPO_ROOT}; "
        f"tried {', '.join(TEMPLATE_DIR_NAMES)}"
    )
    assert TEMPLATES, f"No YAML templates found under {TEMPLATE_ROOT}"
    assert SEARCH_TEMPLATES, (
        f"No search templates recognised under {TEMPLATE_ROOT}; "
        "test_shipped_search_template_loads, the only case that runs the "
        "production loader, would collect nothing and pass"
    )


@pytest.mark.parametrize("path", TEMPLATES, ids=_template_id)
def test_shipped_template_parses(path):
    """The file is YAML, and every document in it is a mapping."""
    documents = _load_documents(path)

    assert documents, "file contains no YAML documents"

    for index, document in enumerate(documents):
        assert isinstance(document, dict), (
            f"document {index + 1} must be a mapping at the root level"
        )


@pytest.mark.parametrize("path", TEMPLATES, ids=_template_id)
def test_shipped_template_has_a_recognised_root(path):
    """A creation template has a `tasks` root, a search template a `search` key.

    The creation side is checked structurally rather than through
    `create_tasks_from_config`, which resolves projects and users against a
    live instance before it looks at the tasks.
    """
    documents = _load_documents(path)
    kind = _template_kind(path, documents)

    assert kind is not None, (
        "unrecognised template: no directory naming its kind, and the root "
        f"keys {sorted(documents[0])} are neither tasks nor search"
    )

    if kind == "create":
        assert len(documents) == 1, "a creation template is a single document"

        root = documents[0]
        unknown = set(root) - CREATE_ROOT_KEYS

        assert not unknown, (
            f"unknown root key(s) {sorted(unknown)}; "
            f"a creation template holds {sorted(CREATE_ROOT_KEYS)}"
        )
        assert "tasks" in root, "a creation template needs a tasks root key"

        # The key is optional - a template that defines no variables renders
        # what it has - but one that is written has to be a mapping of name
        # to value, since that is what the rendering is given
        variables = root.get("variables")

        assert variables is None or isinstance(variables, dict), (
            f"variables must be a mapping, not {type(variables).__name__}"
        )

        _check_task_list(root["tasks"], "tasks")
    else:
        for index, document in enumerate(documents):
            search = document.get("search")

            assert isinstance(search, dict) and search, (
                f"document {index + 1} needs a non-empty search section"
            )


@pytest.mark.parametrize("path", SEARCH_TEMPLATES, ids=_template_id)
def test_shipped_search_template_loads(path):
    """The loader the CLI uses accepts the template and every key in it."""
    maniphest = Maniphest.__new__(Maniphest)

    configs = maniphest._load_search_config(str(path))

    assert configs, "template loaded to no search configurations"

    for config in configs:
        assert config["search"], "search configuration is empty"
