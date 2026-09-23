# -*- coding: utf-8 -*-

"""The new spec loader reads the shipped corpus exactly as the old code does.

Phase 1 of #463 builds phabfive/spec/ beside the two loaders it will replace
- Maniphest._load_search_config and Maniphest.create_tasks_from_yaml - and
does not wire it in. The promise that it *can* replace them is this file: an
equivalence assertion over the real templates, not an eyeball over a
hand-written fixture.

The assertion is made twice: at the document level, which is the layer #468
ships, and at the `Spec` level, which is what every later pass holds and
what `maniphest search --with` will actually be handed in Phase 3.
"""

from pathlib import Path

import pytest

from phabfive.maniphest import Maniphest
from phabfive.spec import load_spec, parse_spec
from phabfive.spec.loader import load_documents

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

SEARCH_TEMPLATES = sorted((TEMPLATES / "task-search").glob("*.yaml"))
CREATE_TEMPLATES = sorted(
    path
    for pattern in ("*.yaml", "*.yml")
    for path in (TEMPLATES / "task-create").glob(pattern)
)


def test_the_corpus_is_there():
    """Guard the guard: an empty glob would make everything below vacuous."""
    assert len(SEARCH_TEMPLATES) >= 8
    assert len(CREATE_TEMPLATES) >= 3


@pytest.mark.parametrize("path", SEARCH_TEMPLATES, ids=lambda path: path.name)
def test_the_loader_matches_load_search_config(path):
    """Every shipped search template, through both readers, same answer.

    _load_search_config is the whole backward-compatibility promise for the
    search kind, and three of these files are multi-document, which is the
    case a single-document loader would silently truncate.
    """
    legacy = Maniphest.__new__(Maniphest)._load_search_config(str(path))

    documents = load_documents(path)

    assert [
        {
            "search": document.get("search", {}),
            "title": document.get("title"),
            "description": document.get("description"),
        }
        for document in documents
    ] == legacy


@pytest.mark.parametrize("path", CREATE_TEMPLATES, ids=lambda path: path.name)
def test_the_loader_matches_the_create_reader(path):
    """Every shipped create template parses to what create_tasks_from_yaml sees.

    templates/task-create/test-template-v2.yml is the one that matters:
    it uses YAML anchors (&WORKGROUP / *WORKGROUP), which resolve only
    because ruamel is still the YAML reader. Routing YAML through anything
    else would break this and nothing else would notice.
    """
    from ruamel.yaml import YAML

    with open(path, encoding="utf-8") as stream:
        legacy = YAML().load(stream)

    documents = load_documents(path)

    assert len(documents) == 1
    assert documents[0] == legacy


def test_yaml_anchors_still_resolve():
    """Named separately, because the assertion above would pass on two Nones."""
    path = TEMPLATES / "task-create" / "test-template-v2.yml"

    document = load_documents(path)[0]

    anchored = document["variables"]["workgroup"]
    assert anchored == ["hholm", "grok"]

    # The alias, not the anchor: a reader that dropped alias support would
    # leave a string or a None here, and every other assertion would pass
    aliased = [
        task.get("subscribers") for task in document["tasks"] if task.get("subscribers")
    ]
    assert aliased == [anchored]


@pytest.mark.parametrize("path", SEARCH_TEMPLATES, ids=lambda path: path.name)
def test_the_spec_matches_load_search_config(path):
    """The same promise one layer up, over the object callers hold.

    `kind="search"` is what the legacy call site passes and what makes a
    template carrying only a banner - no `search:` key at all - keep
    loading: inference alone could not tell that from a create spec.
    """
    legacy = Maniphest.__new__(Maniphest)._load_search_config(str(path))

    spec = load_spec(path, kind="search")

    assert [
        {
            "search": item.get("search", {}),
            "title": item.get("title"),
            "description": item.get("description"),
        }
        for item in spec.items("search")
    ] == legacy


def test_a_document_with_no_search_keys_is_still_one_search(tmp_path):
    """The shape the corpus does not have, pinned anyway.

    Every shipped template writes `search:` in every document, so the corpus
    cannot see a reader that folds a document away. `_load_search_config`
    answers *every* document with a config, including one carrying only
    `variables:`, and running an unconstrained search is a visible thing to
    do - so the spec loader must count the documents the same way.
    """
    path = tmp_path / "search.yaml"
    path.write_text(
        "variables:\n  who: alice\n---\ntitle: One\nsearch:\n  status: open\n",
        encoding="utf-8",
    )

    legacy = Maniphest.__new__(Maniphest)._load_search_config(str(path))
    spec = load_spec(path, kind="search")

    assert len(legacy) == 2
    assert [
        {
            "search": item.get("search", {}),
            "title": item.get("title"),
            "description": item.get("description"),
        }
        for item in spec.items("search")
    ] == legacy


def test_a_banner_only_template_still_loads():
    """A search template may carry no `search:` key at all.

    `data.get("search", {})` in `_load_search_config` has always allowed it,
    and kind inference cannot: a document with only `title:` could be
    anything. `kind="search"` is the answer, which is why the legacy call
    sites pass it.
    """
    spec = parse_spec("title: Just a banner\n", format="yaml", kind="search")

    assert [item.get("search") for item in spec.items("search")] == [{}]
