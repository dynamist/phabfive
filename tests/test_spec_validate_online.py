# -*- coding: utf-8 -*-

"""Online spec validation (#473).

Layer 2 asks the instance about everything a spec names, and the rule it
exists for is that **every** unresolvable reference comes back in one
report. A spec with four bad names costs one run, not four.

The app is mocked the way `tests/test_create_template_users.py` does it:
`Phabfive.__init__` is faked, `.phab` is a `MagicMock`, and a mock that
fails raises a phabfive exception type - never `phabricator.APIError`, which
only `phabfive/conduit.py` ever sees.
"""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.exceptions import (
    PhabfiveAPIException,
    PhabfiveConnectionException,
    PhabfiveDataException,
)
from phabfive.maniphest.core import Maniphest
from phabfive.spec import Problem, Spec, validate_online
from phabfive.spec.online import (
    DEFAULT_RESOLVERS,
    REFERENCE_KINDS,
    ManiphestUserResolver,
    ReferenceIndex,
    ResolveResult,
    field_kind,
    index_references,
)
from phabfive.spec.references import Reference, RefKind
from phabfive.spec.registry import FieldKind

USERS = {"alice": "PHID-USER-alice", "bob": "PHID-USER-bob"}
CALLER = {"phid": "PHID-USER-caller", "userName": "caller"}


def _phab(users_called_me=()):
    """A client that knows alice and bob, and whoever `users_called_me` adds."""
    phab = MagicMock()
    phab.user.whoami.return_value = dict(CALLER)

    records = [
        {"phid": phid, "fields": {"username": name}} for name, phid in USERS.items()
    ] + [{"phid": phid, "fields": {"username": "me"}} for phid in users_called_me]

    def search(constraints):
        names = {name.casefold() for name in constraints.get("usernames", [])}
        phids = set(constraints.get("phids", []))
        return {
            "data": [
                record
                for record in records
                if record["fields"]["username"].casefold() in names
                or record["phid"] in phids
            ]
        }

    phab.user.search.side_effect = search
    return phab


def _app(phab):
    """An already-constructed app, which is all `validate_online` ever takes."""
    with patch("phabfive.maniphest.core.Phabfive.__init__", return_value=None):
        app = Maniphest()

    app.phab = phab
    app.url = "http://phorge.localhost"
    app.conf = {}
    return app


def _spec(*tasks):
    """A create spec, one `tasks:` item per mapping given."""
    return Spec.from_data(
        {
            "kind": "create",
            "tasks": [{"title": f"Task {i}", **task} for i, task in enumerate(tasks)],
        },
        source="<test>",
    )


def _validate(phab, spec, **kwargs):
    return validate_online(spec, _app(phab), **kwargs)


def _codes(problems):
    return [(p.object, p.field, p.value, p.code) for p in problems]


class FakeResolver:
    """A resolver that answers from a dict, and counts its calls."""

    def __init__(self, kind, answers, missing=()):
        self.kind = kind
        self._answers = answers
        self._missing = set(missing)
        self.calls = []

    def resolve(self, app, values):
        self.calls.append(tuple(values))
        return {
            value: self._answers[value]
            for value in values
            if value not in self._missing and value in self._answers
        }


# --------------------------------------------------------------------------
# The rule: one report, however many things are wrong
# --------------------------------------------------------------------------


def test_four_bad_references_are_four_problems_from_one_call():
    """The acceptance criterion: four bad names, one run, four problems."""
    spec = _spec(
        {"assignment": "nosuch1", "subscribers": ["nosuch2"]},
        {"assignment": "nosuch3", "subscribers": ["nosuch4"]},
    )
    phab = _phab()

    problems = _validate(phab, spec)

    assert _codes(problems) == [
        ("tasks[0]", "subscribers[0]", "nosuch2", "unknown-user"),
        ("tasks[0]", "assignment", "nosuch1", "unknown-user"),
        ("tasks[1]", "subscribers[0]", "nosuch4", "unknown-user"),
        ("tasks[1]", "assignment", "nosuch3", "unknown-user"),
    ]
    assert phab.user.search.call_count == 1


def test_a_bad_reference_does_not_stop_the_ones_after_it():
    """Nothing short-circuits: the good names are still resolved."""
    spec = _spec({"assignment": "nosuch", "subscribers": ["alice"]})

    assert [p.value for p in _validate(_phab(), spec)] == ["nosuch"]


def test_every_problem_names_object_field_value_and_why():
    spec = _spec({"assignment": "nosuch"})

    [problem] = _validate(_phab(), spec)

    assert isinstance(problem, Problem)
    assert problem.object == "tasks[0]"
    assert problem.field == "assignment"
    assert problem.value == "nosuch"
    assert problem.code == "unknown-user"
    assert "nosuch" in problem.reason
    assert problem.layer == "online"
    assert problem.severity == "error"
    assert problem.as_record()["layer"] == "online"


def test_a_clean_spec_reports_nothing():
    spec = _spec({"assignment": "alice", "subscribers": ["@bob"]})

    assert _validate(_phab(), spec) == []


def test_problems_come_back_in_document_order_across_kinds():
    """Document order, not resolver order, is what makes the report testable."""
    spec = _spec(
        {"assignment": "nosuch1", "projects": ["#nope"]},
        {"assignment": "nosuch2"},
    )
    project = FakeResolver(
        FieldKind.PROJECT,
        {"#nope": ResolveResult(value="#nope", problem="unknown-project")},
    )

    problems = _validate(_phab(), spec, resolvers=[ManiphestUserResolver(), project])

    assert _codes(problems) == [
        ("tasks[0]", "projects[0]", "#nope", "unknown-project"),
        ("tasks[0]", "assignment", "nosuch1", "unknown-user"),
        ("tasks[1]", "assignment", "nosuch2", "unknown-user"),
    ]


def test_a_nested_subtask_is_reported_under_its_own_path():
    spec = _spec(
        {
            "assignment": "alice",
            "tasks": [{"title": "child", "assignment": "nosuch"}],
        }
    )

    assert _codes(_validate(_phab(), spec)) == [
        ("tasks[0].tasks[0]", "assignment", "nosuch", "unknown-user")
    ]


# --------------------------------------------------------------------------
# One lookup per distinct name, however many items name it
# --------------------------------------------------------------------------


def test_a_name_used_five_times_is_asked_about_once_and_reported_five_times():
    spec = _spec(*({"assignment": "nosuch"} for _ in range(5)))
    phab = _phab()

    problems = _validate(phab, spec)

    assert len(problems) == 5
    assert [p.object for p in problems] == [f"tasks[{i}]" for i in range(5)]
    assert phab.user.search.call_count == 1
    assert phab.user.search.call_args.kwargs["constraints"] == {"usernames": ["nosuch"]}


def test_every_username_in_the_spec_goes_into_one_search():
    spec = _spec(
        {"assignment": "alice", "subscribers": ["@bob"]},
        {"assignment": "nosuch"},
    )
    phab = _phab()

    _validate(phab, spec)

    assert phab.user.search.call_count == 1
    assert phab.user.search.call_args.kwargs["constraints"] == {
        "usernames": ["alice", "bob", "nosuch"]
    }


def test_a_spec_naming_no_user_asks_nothing():
    spec = _spec({"description": "nothing to resolve"})
    phab = _phab()

    assert _validate(phab, spec) == []
    phab.user.search.assert_not_called()
    phab.user.whoami.assert_not_called()


# --------------------------------------------------------------------------
# A server that could not be asked is not a bad reference
# --------------------------------------------------------------------------


def test_a_connection_failure_is_raised_not_reported():
    """The one thing this layer must never do: blame the spec for the network."""
    spec = _spec({"assignment": "alice"})
    phab = _phab()
    phab.user.search.side_effect = PhabfiveConnectionException(
        "Failed to connect to http://phorge.localhost"
    )

    with pytest.raises(PhabfiveConnectionException):
        _validate(phab, spec)


def test_a_conduit_error_is_raised_not_reported():
    spec = _spec({"assignment": "alice"})
    phab = _phab()
    phab.user.search.side_effect = PhabfiveAPIException(
        "ERR-INVALID-AUTH", "API token invalid"
    )

    with pytest.raises(PhabfiveAPIException):
        _validate(phab, spec)


def test_a_whoami_failure_is_raised_not_reported():
    spec = _spec({"assignment": "@me"})
    phab = _phab()
    phab.user.whoami.side_effect = PhabfiveConnectionException("Connection refused")

    with pytest.raises(PhabfiveConnectionException):
        _validate(phab, spec)


def test_a_failed_run_reports_no_problems_at_all():
    """Not one problem and then an exception: the report is all or nothing."""
    spec = _spec({"assignment": "nosuch"}, {"assignment": "alsonosuch"})
    phab = _phab()
    phab.user.search.side_effect = PhabfiveConnectionException("down")

    with pytest.raises(PhabfiveConnectionException):
        _validate(phab, spec)


# --------------------------------------------------------------------------
# Nothing is written, whether validation passes or fails
# --------------------------------------------------------------------------


@pytest.mark.parametrize("assignment", ["alice", "nosuch"])
def test_nothing_is_written(assignment):
    spec = _spec({"assignment": assignment})
    phab = _phab()

    _validate(phab, spec)

    phab.maniphest.edit.assert_not_called()
    phab.user.edit.assert_not_called()
    phab.project.edit.assert_not_called()

    asked = {name for name, _args, _kwargs in phab.mock_calls}
    assert asked <= {"user.search", "user.whoami"}


def test_nothing_is_written_when_the_lookup_itself_fails():
    spec = _spec({"assignment": "nosuch"})
    phab = _phab()
    phab.user.search.side_effect = PhabfiveConnectionException("down")

    with pytest.raises(PhabfiveConnectionException):
        _validate(phab, spec)

    phab.maniphest.edit.assert_not_called()


# --------------------------------------------------------------------------
# The user resolver's spellings
# --------------------------------------------------------------------------


def test_at_me_resolves_through_whoami():
    spec = _spec({"assignment": "@me"})
    phab = _phab()

    assert _validate(phab, spec) == []
    phab.user.whoami.assert_called_once()


def test_at_me_is_the_caller_when_a_user_is_called_me():
    """A username does not take the keyword from everybody else (#496)."""
    spec = _spec({"assignment": "@me"})
    phab = _phab(users_called_me=["PHID-USER-me"])

    assert _validate(phab, spec) == []


def test_a_bare_me_is_the_user_called_me():
    """The escape hatch: the sigil is what makes `@me` a keyword."""
    spec = _spec({"assignment": "me"})
    phab = _phab(users_called_me=["PHID-USER-me"])

    assert _validate(phab, spec) == []


def test_a_user_called_me_does_not_hide_a_real_problem():
    spec = _spec({"assignment": "@me", "subscribers": ["nosuch"]})
    phab = _phab(users_called_me=["PHID-USER-me"])

    assert [p.code for p in _validate(phab, spec)] == ["unknown-user"]


def test_at_me_costs_whoami_and_nothing_else():
    """It used to put "me" into the username search, to catch the ambiguity.

    With `@me` a keyword there is nothing to disambiguate, so the search asks
    only about the names the spec actually wrote (#496).
    """
    spec = _spec({"assignment": "@me", "subscribers": ["alice"]})
    phab = _phab()

    _validate(phab, spec)

    assert phab.user.search.call_count == 1
    assert phab.user.search.call_args.kwargs["constraints"] == {"usernames": ["alice"]}
    phab.user.whoami.assert_called_once()


def test_at_me_is_a_problem_when_the_instance_gives_no_phid():
    spec = _spec({"assignment": "@me"})
    phab = _phab()
    phab.user.whoami.return_value = {}

    [problem] = _validate(phab, spec)

    assert problem.code == "unknown-user"


def test_a_leading_at_is_optional():
    spec = _spec({"assignment": "@alice", "subscribers": ["bob"]})

    assert _validate(_phab(), spec) == []


def test_usernames_are_matched_case_insensitively():
    spec = _spec({"assignment": "ALICE"})

    assert _validate(_phab(), spec) == []


def test_a_user_phid_is_asked_about_rather_than_passed_through():
    spec = _spec({"assignment": "PHID-USER-nope", "subscribers": ["PHID-USER-alice"]})
    phab = _phab()

    [problem] = _validate(phab, spec)

    assert (problem.field, problem.value, problem.code) == (
        "assignment",
        "PHID-USER-nope",
        "unknown-user",
    )
    # No username was named, so the usernames search is skipped entirely
    assert phab.user.search.call_count == 1
    assert phab.user.search.call_args.kwargs["constraints"] == {
        "phids": ["PHID-USER-alice", "PHID-USER-nope"]
    }


def test_usernames_and_phids_are_two_searches_not_more():
    spec = _spec({"assignment": "alice", "subscribers": ["PHID-USER-bob"]})
    phab = _phab()

    assert _validate(phab, spec) == []
    assert phab.user.search.call_count == 2


def test_the_resolver_answers_for_every_value_it_is_given():
    """A value left out of the mapping is reported by nobody, so none are."""
    values = ["alice", "@bob", "@me", "PHID-USER-bob", "nosuch"]

    answers = ManiphestUserResolver().resolve(_app(_phab()), values)

    assert set(answers) == set(values)
    assert answers["nosuch"].problem == "unknown-user"
    assert answers["alice"].phid == "PHID-USER-alice"
    assert answers["alice"].resolved is True


# --------------------------------------------------------------------------
# The reference index
# --------------------------------------------------------------------------


def test_the_index_keeps_document_order_and_distinct_values():
    spec = _spec(
        {"assignment": "alice", "subscribers": ["bob"]},
        {"assignment": "alice"},
    )

    index = index_references(spec)

    assert [(r.object, r.field, r.value) for r in index.references] == [
        ("tasks[0]", "subscribers[0]", "bob"),
        ("tasks[0]", "assignment", "alice"),
        ("tasks[1]", "assignment", "alice"),
    ]
    assert index.values(FieldKind.USER) == ("bob", "alice")
    assert len(index.sites(FieldKind.USER, "alice")) == 2
    assert len(index) == 3
    assert bool(index) is True


def test_the_index_indexes_the_elements_of_a_list_valued_key():
    spec = _spec({"subscribers": ["alice", "bob"]})

    assert [(r.field, r.value) for r in index_references(spec).references] == [
        ("subscribers[0]", "alice"),
        ("subscribers[1]", "bob"),
    ]


def test_an_unrendered_variable_is_not_a_reference():
    """The offline pass owns "who is undefined"; reporting it twice helps nobody."""
    spec = _spec({"assignment": "{{ who }}", "subscribers": ["nosuch"]})
    phab = _phab()

    assert [p.value for p in _validate(phab, spec)] == ["nosuch"]
    assert phab.user.search.call_args.kwargs["constraints"] == {"usernames": ["nosuch"]}


def test_a_value_of_the_wrong_type_is_left_to_the_offline_pass():
    spec = _spec({"assignment": 17, "subscribers": [{"a": 1}]})

    assert index_references(spec).references == ()


def test_a_local_id_is_not_asked_of_the_instance():
    """`$parent` names something this spec creates; that is offline's question."""
    spec = _spec(
        {"id": "parent"},
        {"parents": ["$parent"], "assignment": "nosuch"},
    )
    phab = _phab()

    assert index_references(spec).values(FieldKind.MONOGRAM) == ()
    assert [p.value for p in _validate(phab, spec)] == ["nosuch"]


def test_an_empty_index_asks_nothing_and_reports_nothing():
    index = ReferenceIndex()

    assert index.kinds() == ()
    assert index.values(FieldKind.USER) == ()
    assert index.sites(FieldKind.USER, "alice") == ()
    assert list(index.resolvable()) == []
    assert bool(index) is False
    assert "none" in repr(index)


def test_every_kind_that_needs_the_instance_is_a_reference_kind():
    assert REFERENCE_KINDS == {
        FieldKind.USER,
        FieldKind.PROJECT,
        FieldKind.SPACE,
        FieldKind.POLICY,
        FieldKind.MONOGRAM,
        FieldKind.INSTANCE_ENUM,
    }


# --------------------------------------------------------------------------
# The field decides where a name is looked up, not its spelling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("assignment", FieldKind.USER),
        ("subscribers[3]", FieldKind.USER),
        ("projects[0]", FieldKind.PROJECT),
        ("space", FieldKind.SPACE),
        ("parent", FieldKind.MONOGRAM),
        ("parents[0]", FieldKind.MONOGRAM),
        ("subtasks[1]", FieldKind.MONOGRAM),
        # A key nothing declares at all. `title:` used to stand here and no
        # longer can: it is a declared create field now, so it answers with
        # its own kind, and what this row is for is the key that answers
        # with nothing.
        ("nonsense", None),
    ],
)
def test_the_field_says_what_a_reference_names(name, expected):
    reference = Reference(
        object="tasks[0]",
        field=name,
        value="alice",
        kind=RefKind.NAME,
        object_type="task",
    )

    assert field_kind(reference) is expected


def test_a_bare_name_in_assignment_is_a_user_not_a_project():
    """RefKind.NAME is a spelling; where to look it up is the field's job."""
    spec = _spec({"assignment": "alice", "projects": ["alice"]})

    index = index_references(spec)

    assert index.values(FieldKind.USER) == ("alice",)
    assert index.values(FieldKind.PROJECT) == ("alice",)


def test_a_local_reference_names_nothing_online():
    reference = Reference(
        object="tasks[1]",
        field="parents[0]",
        value="$parent",
        kind=RefKind.LOCAL,
        object_type="task",
    )

    assert field_kind(reference) is None


# --------------------------------------------------------------------------
# The seams left for Phases 2 and 3
# --------------------------------------------------------------------------


def test_a_kind_with_no_resolver_is_left_unchecked_not_reported_clean():
    """A kind nothing answers for reports nothing rather than a verdict.

    The references are indexed, nobody is asked, and nothing is reported -
    never a clean bill of health for a check that never ran. Written with a
    resolver list that answers only for users, because every kind a create
    spec names now *has* a default resolver: #482 added
    `phabfive.spec.online.TaskResolver`, which is what
    `test_a_monogram_is_answered_by_the_default_resolvers` pins from the
    other side.
    """
    spec = _spec({"parents": ["T999999"], "subtasks": ["T999998"]})
    phab = _phab()

    assert _validate(phab, spec, resolvers=(ManiphestUserResolver(),)) == []
    assert set(index_references(spec).kinds()) == {FieldKind.MONOGRAM}


def test_a_monogram_is_answered_by_the_default_resolvers():
    """`parents:` and `subtasks:` reach the instance since #482.

    A task that does not exist is `unknown-reference` - reported once per
    place it was named, from one `maniphest.search` over every id in the
    document.
    """
    spec = _spec({"parents": ["T999999"], "subtasks": ["T999998"]})
    phab = _phab()
    phab.maniphest.search.return_value = {"data": []}

    problems = _validate(phab, spec)

    assert _codes(problems) == [
        ("tasks[0]", "parents[0]", "T999999", "unknown-reference"),
        ("tasks[0]", "subtasks[0]", "T999998", "unknown-reference"),
    ]
    assert phab.maniphest.search.call_count == 1
    assert phab.maniphest.search.call_args.kwargs["constraints"] == {
        "ids": [999998, 999999]
    }


def test_a_search_spec_s_user_filters_are_resolved():
    """The four user filters of a `searches:` item, asked about once.

    Phase 1 declared create keys only, so a search spec's references were
    walked and nothing was found. They are declared now - and deliberately
    only the four whose value is already a hard failure when it does not
    resolve, so this changes *when* a bad `assigned:` is reported and not
    whether.
    """
    spec = Spec.from_data(
        {
            "kind": "search",
            "searches": [
                {"search": {"assigned": "nosuch,alice", "author": "nosuch"}},
                {"search": {"subscriber": ["alice"], "closed-by": "nosuch"}},
            ],
        },
        source="<test>",
    )
    phab = _phab()

    problems = _validate(phab, spec)

    assert [(one.object, one.field, one.value, one.code) for one in problems] == [
        ("searches[0]", "search.assigned[0]", "nosuch", "unknown-user"),
        ("searches[0]", "search.author", "nosuch", "unknown-user"),
        ("searches[1]", "search.closed-by", "nosuch", "unknown-user"),
    ]

    # One request for every distinct name in the whole spec
    assert phab.user.search.call_count == 1


def test_a_search_spec_s_tag_and_space_are_still_left_alone():
    """Declaring either would change an existing template's exit status.

    A `tag:` matching nothing is a `log.error` and exit 0 today, and a
    `space:` that does not resolve is a `log.warning` and no filter at all.
    Reporting either as a problem is a change worth making and it needs its
    own test and a line in docs/search-templates.md - it is not this.
    """
    spec = Spec.from_data(
        {
            "kind": "search",
            "searches": [{"search": {"tag": "nosuchproject", "space": "S9999"}}],
        },
        source="<test>",
    )
    phab = _phab()

    assert index_references(spec).references == ()
    assert _validate(phab, spec) == []


def test_the_default_resolvers_are_the_kinds_that_have_landed():
    """One entry per lookup that exists, and the icon one is field-scoped."""
    assert [(r.kind, r.fields) for r in DEFAULT_RESOLVERS] == [
        (FieldKind.USER, frozenset()),
        (FieldKind.PROJECT, frozenset()),
        (FieldKind.SPACE, frozenset()),
        (FieldKind.INSTANCE_ENUM, frozenset({"icon"})),
        (FieldKind.MONOGRAM, frozenset()),
    ]


def test_a_caller_supplies_its_own_resolvers():
    spec = _spec({"assignment": "alice"})
    fake = FakeResolver(
        FieldKind.USER,
        {"alice": ResolveResult(value="alice", problem="unknown-user")},
    )
    phab = _phab()

    [problem] = _validate(phab, spec, resolvers=[fake])

    assert fake.calls == [("alice",)]
    assert problem.code == "unknown-user"
    phab.user.search.assert_not_called()


def test_a_supplied_resolver_beats_the_default_for_its_kind():
    spec = _spec({"assignment": "nosuch"})
    fake = FakeResolver(
        FieldKind.USER, {"nosuch": ResolveResult(value="nosuch", phid="PHID-USER-x")}
    )

    assert _validate(_phab(), spec, resolvers=[fake, ManiphestUserResolver()]) == []


def test_a_resolver_that_skips_a_value_reports_nothing_for_it(caplog):
    spec = _spec({"assignment": "alice"})
    fake = FakeResolver(FieldKind.USER, {}, missing=["alice"])

    with caplog.at_level("WARNING"):
        assert _validate(_phab(), spec, resolvers=[fake]) == []

    assert "did not answer" in caplog.text


def test_a_generic_reason_names_the_value_when_a_resolver_gives_none():
    spec = _spec({"assignment": "alice"})
    fake = FakeResolver(
        FieldKind.USER, {"alice": ResolveResult(value="alice", problem="unknown-user")}
    )

    [problem] = _validate(_phab(), spec, resolvers=[fake])

    assert "alice" in problem.reason


def test_an_ambiguous_result_lists_its_candidates_in_the_reason():
    spec = _spec({"projects": ["Platform"]})
    fake = FakeResolver(
        FieldKind.PROJECT,
        {
            "Platform": ResolveResult(
                value="Platform",
                problem="ambiguous-project",
                candidates=("#platform-a", "#platform-b"),
            )
        },
    )

    [problem] = _validate(_phab(), spec, resolvers=[fake])

    assert "#platform-a" in problem.reason
    assert "#platform-b" in problem.reason


def test_a_result_with_neither_a_code_nor_a_phid_is_still_an_answer():
    """`problem` alone tells an answer from a failure, so this one resolved."""
    spec = _spec({"assignment": "alice"})
    fake = FakeResolver(
        FieldKind.USER, {"alice": ResolveResult(value="alice", reason="nope")}
    )

    assert _validate(_phab(), spec, resolvers=[fake]) == []


# --------------------------------------------------------------------------
# Library shape
# --------------------------------------------------------------------------


def test_validate_online_is_part_of_the_subpackage_surface():
    import phabfive.spec

    assert "validate_online" in phabfive.spec.__all__
    assert phabfive.spec.validate_online is validate_online


def test_validate_online_constructs_nothing():
    """It takes an app so a frontend can hand it per-request credentials.

    Patching `phabfive.core.Phabricator` alone proves little any more:
    constructing a `Phabfive` no longer builds a client, because the Conduit
    wrapper defers that to first use, so the assertion would pass even if
    this layer *did* construct an app. `Phabfive.__init__` itself is the
    thing that must not run - it is what reads `~/.arcrc`, `.arcconfig` and
    the environment.
    """
    spec = _spec({"assignment": "alice"})
    app = _app(_phab())

    def refuse(*args, **kwargs):  # pragma: no cover - the failure we guard
        raise AssertionError("validate_online constructed an app")

    with patch("phabfive.core.Phabricator") as client:
        with patch("phabfive.core.Phabfive.__init__", refuse):
            assert validate_online(spec, app) == []

    client.assert_not_called()


def test_every_user_is_asked_about_however_many_pages_it_takes():
    """Conduit answers a search with at most 100 rows, plus a cursor.

    This layer's whole point is that every distinct name in the spec goes
    into ONE `user.search`, which is exactly the call that runs into the
    page cap - a spec naming 150 users would report 50 of them as
    `unknown-user` if only the first page were read. That is a lookup only
    partly made, reported as an absence.
    """
    names = [f"user{index:03d}" for index in range(150)]
    records = [
        {"phid": f"PHID-USER-{name}", "fields": {"username": name}} for name in names
    ]

    phab = MagicMock()
    phab.user.whoami.return_value = dict(CALLER)

    def search(constraints, **kwargs):
        wanted = {name.casefold() for name in constraints.get("usernames", [])}
        matching = [
            record for record in records if record["fields"]["username"] in wanted
        ]
        start = names.index(kwargs["after"]) + 1 if kwargs.get("after") else 0
        page = matching[start : start + 100]
        after = (
            page[-1]["fields"]["username"]
            if len(matching) > start + len(page)
            else None
        )
        return {"data": page, "cursor": {"after": after}}

    phab.user.search.side_effect = search

    spec = _spec(*[{"assignment": name} for name in names])

    assert _validate(phab, spec) == []
    assert phab.user.search.call_count == 2


def test_one_page_of_users_is_still_one_request():
    """The cheap case stays cheap: no cursor, no second call."""
    spec = _spec({"assignment": "alice"}, {"assignment": "bob"})
    phab = _phab()

    assert _validate(phab, spec) == []
    assert phab.user.search.call_count == 1


def test_a_result_compares_by_value():
    assert ResolveResult(value="a") == ResolveResult(value="a")
    assert ResolveResult(value="a", phid="p").resolved is True
    assert ResolveResult(value="a", problem="unknown-user").resolved is False


def test_a_missing_user_is_reported_not_raised():
    """`phabfive.users` raises there; this layer reports instead."""
    spec = _spec({"assignment": "nosuch"})

    try:
        problems = _validate(_phab(), spec)
    except PhabfiveDataException as e:  # pragma: no cover - the failure we guard
        pytest.fail(f"a missing user was raised, not reported: {e}")

    assert [p.code for p in problems] == ["unknown-user"]
