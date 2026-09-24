# -*- coding: utf-8 -*-

"""The shipped corpus, read the way the command is handed it.

Phase 1 of #463 built phabfive/spec/ beside the two loaders it would replace
and proved it could replace them by equivalence: every shipped template,
through both readers, same answer. Phase 2 (#476) made
`Maniphest._load_search_config` *be* the new loader - it reads the file with
`phabfive.spec.load_spec` and then projects the result into the per-search
mapping the command has always taken - so that equivalence would now be a
function compared with itself.

What replaces it is a frozen record of the corpus, below: how many searches
each shipped template holds, which banner title each one carries, and which
keys its `search:` section names. It is asserted three times, once per layer
a caller can read at - `load_documents`, `Spec.items("search")` and
`_load_search_config` - because those three are what would drift apart.

The number of searches per file is the part that matters most: three of the
shipped files hold four searches each, and a reader that dropped one of them
- or one that stopped folding a `searches:` list into several - would still
answer something plausible for every other assertion here.

The create half is untouched and still an equivalence assertion, because
`create_tasks_from_yaml` has not been ported yet.
"""

from pathlib import Path

import pytest

from phabfive.maniphest import Maniphest
from phabfive.spec import load_spec, parse_spec
from phabfive.spec.loader import load_documents

SPEC_ROOT = Path(__file__).resolve().parent.parent / "specs"

SEARCH_SPECS = sorted((SPEC_ROOT / "search").glob("*.yaml"))
CREATE_SPECS = sorted(
    path
    for pattern in ("*.yaml", "*.yml")
    for path in (SPEC_ROOT / "create").glob(pattern)
)


# One entry per shipped search template, one mapping per search it holds, in
# file order. `title` is the banner title exactly as the file writes it -
# None when its author did not name that search, which is what makes a single
# search go unlabelled - `described` whether there is a banner description,
# and `search` the whole `search:` section, **values included**.
#
# The values are here rather than only the key names because a loader that
# coerced one - `created-after: 14` read as the string "14", a list flattened,
# a scalar that no longer compares equal to a str - would keep every key name
# and answer a different search. Key names alone cannot see that.
#
# Written out rather than generated, so that a change to what the loader
# reads has to be typed here on purpose.
EXPECTED_SEARCHES = {
    "blocked-tasks.yaml": (
        {
            "title": "🚫 Blocked or Previously Blocked",
            "described": True,
            "search": {
                "column": "in:Blocked,been:Waiting,been:Blocked",
                "show-history": True,
            },
        },
    ),
    "development-workflow-audit.yaml": (
        {
            "title": "🔄 Tasks Ready for Review",
            "described": True,
            "search": {
                "column": "in:Review,in:Code Review,in:QA",
                "created-after": 14,
                "show-history": True,
            },
        },
        {
            "title": "⚡ Fast-Moving Tasks",
            "described": True,
            "search": {
                "column": "forward",
                "show-history": True,
                "show-metadata": True,
                "updated-after": 3,
            },
        },
        {
            "title": "⏪ Tasks Moving Backward",
            "described": True,
            "search": {
                "column": "backward",
                "show-history": True,
                "updated-after": 7,
            },
        },
        {
            "title": "🏁 Tasks Resolved But Not in Done",
            "described": True,
            "search": {
                "column": "not:in:Done",
                "show-history": False,
                "status": "closed+in:Resolved,closed+in:Duplicate",
            },
        },
    ),
    "escalated-priorities.yaml": (
        {
            "title": "📈 Priority Escalations",
            "described": True,
            "search": {
                "priority": "raised",
                "show-history": True,
                "show-metadata": True,
                "updated-after": 30,
            },
        },
    ),
    # `updated-after` is the one filter in the corpus written as a variable,
    # `stale_days: {default: 14}`, so it is the one frozen value that differs
    # between the layers below: the bytes and the `Spec` carry the template,
    # and what the command loops over carries what it rendered to. Both are
    # frozen, in this table and in RENDERED_SEARCHES.
    "high-priority-stale-tasks.yaml": (
        {
            "title": "🔥 Stale High Priority Tasks",
            "described": True,
            "search": {
                "priority": "in:High,in:Unbreak Now!,in:Triage",
                "show-history": True,
                "show-metadata": True,
                "updated-after": "{{ stale_days }}",
            },
        },
    ),
    "project-status-overview.yaml": (
        {
            "title": "🔥 High Priority Open Tasks",
            "described": True,
            "search": {
                "priority": "in:High,in:Unbreak Now!,in:Triage",
                "show-history": True,
                "status": "in:Open",
                "tag": "*",
            },
        },
        {
            "title": "✅ Recently Resolved Tasks",
            "described": True,
            "search": {
                "show-metadata": True,
                "status": "closed+in:Resolved",
                "tag": "*",
                "updated-after": 7,
            },
        },
        {
            "title": "🚫 Blocked or Stalled Work",
            "described": True,
            "search": {
                "column": "in:Blocked,been:Waiting",
                "show-history": True,
                "tag": "*",
                "updated-after": 14,
            },
        },
        {
            "title": "📈 Priority Escalations",
            "described": True,
            "search": {
                "priority": "raised",
                "show-history": True,
                "tag": "*",
                "updated-after": 30,
            },
        },
    ),
    "recently-moved-to-review.yaml": (
        {
            "title": "🔄 Recently Moved to Review",
            "described": True,
            "search": {
                "column": "to:Review,to:Code Review,to:QA",
                "created-after": 7,
                "show-history": True,
            },
        },
    ),
    "tasks-resolved-but-not-in-done.yaml": (
        {
            "title": "🏁 Resolved But Not in Done",
            "described": True,
            "search": {
                "column": "not:in:Done",
                "show-history": True,
                "status": "closed+in:Resolved,closed+in:Duplicate",
                "tag": "*",
            },
        },
    ),
    "team-productivity-report.yaml": (
        {
            "title": "📊 Work Created This Week",
            "described": True,
            "search": {
                "created-after": 7,
                "show-metadata": True,
                "tag": "*",
            },
        },
        {
            "title": "🎯 Work Completed This Week",
            "described": True,
            "search": {
                "show-history": False,
                "status": "closed+in:Resolved",
                "tag": "*",
                "updated-after": 7,
            },
        },
        {
            "title": "🔥 High Priority Workload",
            "described": True,
            "search": {
                "priority": "in:High,in:Unbreak Now!,in:Triage",
                "show-metadata": True,
                "status": "in:Open",
                "tag": "*",
            },
        },
        {
            "title": "⌛ Long-Running Tasks",
            "described": True,
            "search": {
                "created-after": 60,
                "show-metadata": True,
                "status": "in:Open",
                "tag": "*",
            },
        },
    ),
}


#: The eight files the record above freezes: the search templates that
#: predate the spec format and were migrated into it by #489. The four
#: written for the format itself (#490) are deliberately not here - this
#: file pins a *migration*, not the corpus, and every shipped spec is loaded
#: and validated by tests/test_spec_corpus.py whether or not it has an entry.
FROZEN_SEARCHES = [path for path in SEARCH_SPECS if path.name in EXPECTED_SEARCHES]


def _summary(searches):
    """The frozen shape, from a list of per-search mappings.

    The `search:` section is turned into a plain dict of plain values, so an
    assertion failure reads as data rather than as ruamel repr - and so that
    a ruamel scalar and the str it was written as compare as the same thing
    while an int that became a str does not.
    """
    return [
        {
            "title": search.get("title"),
            "described": search.get("description") is not None,
            "search": {
                str(key): (str(value) if isinstance(value, str) else value)
                for key, value in (search.get("search") or {}).items()
            },
        }
        for search in searches
    ]


def test_the_corpus_is_there():
    """Guard the guard: an empty glob would make everything below vacuous."""
    assert len(SEARCH_SPECS) >= 8
    assert len(CREATE_SPECS) >= 3
    assert len(FROZEN_SEARCHES) >= 8


def test_every_frozen_name_is_a_file_on_disk():
    """A record entry naming nothing would be asserted against nothing.

    The other direction is not asserted: a search spec written *for* the
    format needs no entry here, and requiring one would mean typing a frozen
    record for every example added from now on. What must not happen is an
    entry going stale - a file renamed or removed while its expectation sits
    here looking like coverage - and that is what this catches.
    """
    missing = sorted(set(EXPECTED_SEARCHES) - {path.name for path in SEARCH_SPECS})

    assert missing == [], f"frozen record names files that are gone: {missing}"
    assert len(FROZEN_SEARCHES) == len(EXPECTED_SEARCHES)


@pytest.mark.parametrize("path", FROZEN_SEARCHES, ids=lambda path: path.name)
def test_the_document_loader_reads_the_frozen_corpus(path):
    """The bottom layer: the bytes on disk, with nothing interpreted.

    Every shipped file is one document now - #489 folded the `---`-separated
    ones into a `searches:` list - so what this asserts is that the raw
    document carries the envelope and the same searches in the same order,
    before any envelope splitting or kind inference has run. A loader that
    reordered the list, or dropped the item a `---` used to separate, would
    be caught here rather than one layer up where `Spec` could paper over it.
    """
    documents = load_documents(path)

    assert len(documents) == 1

    document = documents[0]

    assert document["spec"] == "phorge/v1alpha1"
    assert document["kind"] == "search"
    assert _summary(document["searches"]) == list(EXPECTED_SEARCHES[path.name])


@pytest.mark.parametrize("path", FROZEN_SEARCHES, ids=lambda path: path.name)
def test_the_spec_reads_the_frozen_corpus(path):
    """The object every later pass holds.

    `kind="search"` is what the call sites pass and what makes a template
    carrying only a banner - no `search:` key at all - keep loading:
    inference alone could not tell that from a create spec.
    """
    spec = load_spec(path, kind="search")

    assert _summary(spec.items("search")) == list(EXPECTED_SEARCHES[path.name])


#: What the third layer sees where it differs from the two below it: the
#: command renders a spec's variables before it reads the filters, the same
#: way `phabfive.cli.search_spec.load_search_spec` does for the other four
#: `--with` sites. A file not named here renders to itself.
RENDERED_SEARCHES = {
    "high-priority-stale-tasks.yaml": (
        {
            "title": "🔥 Stale High Priority Tasks",
            "described": True,
            "search": {
                "priority": "in:High,in:Unbreak Now!,in:Triage",
                "show-history": True,
                "show-metadata": True,
                "updated-after": "14",
            },
        },
    ),
}


@pytest.mark.parametrize("path", FROZEN_SEARCHES, ids=lambda path: path.name)
def test_the_command_is_handed_the_frozen_corpus(path):
    """What `maniphest search --with` actually loops over.

    `_load_search_config` reads the file with `load_spec` now, so this is no
    longer a second implementation - but it still projects the result, and a
    projection that lost a title or a document would be invisible one layer
    down.
    """
    configs = Maniphest.__new__(Maniphest)._load_search_config(str(path))
    expected = RENDERED_SEARCHES.get(path.name, EXPECTED_SEARCHES[path.name])

    assert _summary(configs) == list(expected)
    # `type` is in the projection: without it `plan_search` sees None, falls
    # back to "task" and runs a `type: paste` item as a task search.
    assert all(
        set(config) == {"type", "search", "title", "description"} for config in configs
    )


@pytest.mark.parametrize("path", CREATE_SPECS, ids=lambda path: path.name)
def test_the_loader_matches_the_create_reader(path):
    """Every shipped create spec parses to what create_tasks_from_yaml sees.

    specs/create/feature-epic.yaml is the one that matters:
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
    path = SPEC_ROOT / "create" / "feature-epic.yaml"

    document = load_documents(path)[0]

    anchored = document["variables"]["workgroup"]
    assert anchored == ["gabriel.blomqvist", "sebastian.soderberg"]

    # The alias, not the anchor: a reader that dropped alias support would
    # leave a string or a None here, and every other assertion would pass
    aliased = [
        task.get("subscribers") for task in document["tasks"] if task.get("subscribers")
    ]
    assert aliased == [anchored]


def test_a_document_with_no_search_keys_is_still_one_search(tmp_path):
    """The shape the corpus does not have, pinned anyway.

    Every shipped template writes `search:` in every document, so the corpus
    cannot see a reader that folds a document away. A document carrying only
    `variables:` is still a search - an unconstrained one, which is a visible
    thing to run - so the count has to be two, not one.
    """
    path = tmp_path / "search.yaml"
    path.write_text(
        "variables:\n  who: alice\n---\ntitle: One\nsearch:\n  status: open\n",
        encoding="utf-8",
    )

    configs = Maniphest.__new__(Maniphest)._load_search_config(str(path))
    spec = load_spec(path, kind="search")

    assert _summary(configs) == [
        {"title": None, "described": False, "search": {}},
        {"title": "One", "described": False, "search": {"status": "open"}},
    ]
    # Not `_summary(spec.items("search")) == _summary(configs)`: the
    # projection is built from `spec.items("search")`, so that would be one
    # expression compared with itself. The frozen list above is the
    # expectation, and this says the other layer reads it the same way.
    assert _summary(spec.items("search")) == [
        {"title": None, "described": False, "search": {}},
        {"title": "One", "described": False, "search": {"status": "open"}},
    ]


def test_a_banner_only_template_still_loads():
    """A search template may carry no `search:` key at all.

    `data.get("search", {})` in the legacy reader always allowed it, and kind
    inference cannot: a document with only `title:` could be anything.
    `kind="search"` is the answer, which is why the call sites pass it.
    """
    spec = parse_spec("title: Just a banner\n", format="yaml", kind="search")

    assert [item.get("search") for item in spec.items("search")] == [{}]
