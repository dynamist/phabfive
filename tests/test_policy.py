# -*- coding: utf-8 -*-

"""The policy grammar, exercised on its own.

Deliberately not through a CLI: repository policies are the first caller and
task policies will be the next, and a grammar that only one command's tests
describe is a grammar the next command gets to rediscover.
"""

# 3rd party imports
from unittest.mock import MagicMock

import pytest

# phabfive imports
from phabfive.constants import POLICY_KEYWORDS
from phabfive.exceptions import PhabfiveConfigException, PhabfiveDataException
from phabfive.policy import (
    PHID_QUERY_CHUNK,
    POLICY_GRAMMAR,
    policy_label,
    policy_lockout_message,
    resolve_policy_names,
    resolve_policy_value,
    validate_policy_value,
)


class _Result(dict):
    """What phid.query actually answers with.

    The phabricator library wraps a response in its own Result type, which
    maps like a dict without being one - so an isinstance check against dict
    silently names nothing at all, which is how the first cut of this shipped
    a repo show that still printed raw PHIDs.
    """


def _phab(projects=None, users=None, phids=None):
    phab = MagicMock()
    phab.project.search.return_value = {"data": projects or []}
    phab.user.search.return_value = {"data": users or []}
    phab.phid.query.return_value = _Result(phids or {})
    return phab


class TestKeywords:
    """The four constants Phorge accepts, passed straight through.

    A constant rather than a lookup because there is no policy.query
    endpoint to ask, so nothing can discover them at runtime.
    """

    @pytest.mark.parametrize("keyword", POLICY_KEYWORDS)
    def test_every_keyword_is_accepted_unchanged(self, keyword):
        phab = _phab()

        assert resolve_policy_value(phab, keyword) == keyword
        phab.project.search.assert_not_called()
        phab.user.search.assert_not_called()

    def test_the_keywords_are_the_ones_phorge_names(self):
        assert POLICY_KEYWORDS == ["public", "users", "admin", "no-one"]


class TestGrammar:
    """What is accepted, and what is refused before anything is sent."""

    def test_a_phid_passes_through(self):
        phab = _phab()
        phid = "PHID-PLCY-hs5zc7kbrq6iyqgvzuvv"

        assert resolve_policy_value(phab, phid) == phid
        phab.phid.query.assert_not_called()

    def test_a_project_resolves_through_its_slug(self):
        phab = _phab(projects=[{"phid": "PHID-PROJ-infra"}])

        assert resolve_policy_value(phab, "#infrastructure") == "PHID-PROJ-infra"
        phab.project.search.assert_called_once_with(
            constraints={"slugs": ["infrastructure"]}
        )

    def test_a_user_resolves_through_its_username(self):
        phab = _phab(users=[{"phid": "PHID-USER-admin"}])

        assert resolve_policy_value(phab, "@admin") == "PHID-USER-admin"
        phab.user.search.assert_called_once_with(constraints={"usernames": ["admin"]})

    def test_surrounding_whitespace_is_not_part_of_the_value(self):
        assert resolve_policy_value(_phab(), "  public  ") == "public"

    def test_none_means_not_asked_for(self):
        assert resolve_policy_value(_phab(), None) is None

    def test_an_unknown_word_is_refused(self):
        """And refused here, not by the API: Conduit reads an unrecognised
        value as a policy nobody satisfies, so it answers a typo with a
        self-lockout error rather than with anything about spelling."""
        phab = _phab()

        with pytest.raises(PhabfiveConfigException) as excinfo:
            resolve_policy_value(phab, "nonsense")

        assert "nonsense" in str(excinfo.value)
        phab.project.search.assert_not_called()
        phab.user.search.assert_not_called()

    def test_the_error_names_the_option_it_came_from(self):
        with pytest.raises(PhabfiveConfigException) as excinfo:
            validate_policy_value("nonsense", option="--visible-to")

        assert "--visible-to" in str(excinfo.value)

    @pytest.mark.parametrize("value", ["#", "@", "", "   "])
    def test_a_prefix_with_nothing_after_it_is_refused(self, value):
        with pytest.raises(PhabfiveConfigException):
            validate_policy_value(value)

    def test_validation_is_only_about_shape(self):
        """Whether the project exists is a question for the API."""
        assert validate_policy_value("#nosuchproject") == "#nosuchproject"

    def test_a_project_that_does_not_exist_is_reported_as_such(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            resolve_policy_value(_phab(projects=[]), "#nope")

        assert "#nope" in str(excinfo.value)

    def test_a_user_that_does_not_exist_is_reported_as_such(self):
        with pytest.raises(PhabfiveDataException) as excinfo:
            resolve_policy_value(_phab(users=[]), "@nobody")

        assert "@nobody" in str(excinfo.value)

    def test_a_failing_lookup_is_not_a_traceback(self):
        phab = _phab()
        phab.project.search.side_effect = RuntimeError("boom")

        with pytest.raises(PhabfiveDataException):
            resolve_policy_value(phab, "#infrastructure")


class TestMe:
    """@me is the caller, in every policy option, as it is in --assigned."""

    def _phab(self, users=None):
        phab = _phab(users=users)
        phab.user.whoami.return_value = {
            "phid": "PHID-USER-caller",
            "userName": "caller",
        }
        return phab

    def test_me_resolves_to_the_caller(self):
        """Not to a user called "me", which is what it was looked up as."""
        phab = self._phab()

        assert resolve_policy_value(phab, "@me") == "PHID-USER-caller"
        phab.user.whoami.assert_called_once_with()

    def test_me_is_in_the_grammar(self):
        assert "@me" in POLICY_GRAMMAR
        assert validate_policy_value("@me") == "@me"

    def test_me_is_read_regardless_of_case(self):
        """Phorge usernames are case-insensitive, so @Me is not somebody."""
        assert resolve_policy_value(self._phab(), "@Me") == "PHID-USER-caller"

    def test_a_user_called_me_makes_it_an_error(self):
        """Either reading would hand an object to the wrong person."""
        phab = self._phab(users=[{"phid": "PHID-USER-me"}])

        with pytest.raises(PhabfiveDataException) as excinfo:
            resolve_policy_value(phab, "@me", option="--visible-to")

        message = str(excinfo.value)
        assert "--visible-to" in message
        assert "ambiguous" in message
        assert "PHID-USER-me" in message
        assert "PHID-USER-caller" in message

    def test_a_failing_whoami_is_not_a_traceback(self):
        phab = self._phab()
        phab.user.whoami.side_effect = RuntimeError("boom")

        with pytest.raises(PhabfiveDataException):
            resolve_policy_value(phab, "@me")

    def test_a_username_that_merely_starts_with_me_is_a_user(self):
        phab = self._phab(users=[{"phid": "PHID-USER-meg"}])

        assert resolve_policy_value(phab, "@meg") == "PHID-USER-meg"
        phab.user.whoami.assert_not_called()


class TestNaming:
    """The reverse: a PHID back into something a person can read."""

    def test_a_keyword_is_labelled_the_way_the_web_ui_labels_it(self):
        assert policy_label("public") == "Public (No Login Required)"

    def test_a_missing_policy_is_not_an_empty_string(self):
        assert policy_label(None) == "(none)"

    def test_a_project_is_named_in_the_spelling_the_grammar_takes(self):
        """Its slug, so what a policy is shown as can be typed back in.

        phid.query carries a project's slug only in its URI, the way it
        carries a Space's monogram there.
        """
        phab = _phab(
            phids={
                "PHID-PROJ-infra": {
                    "type": "PROJ",
                    "name": "Infrastructure",
                    "fullName": "Infrastructure",
                    "uri": "http://phorge.localhost/tag/infrastructure/",
                }
            }
        )

        names = resolve_policy_names(phab, ["users", "PHID-PROJ-infra"])

        assert names == {"PHID-PROJ-infra": "#infrastructure"}
        assert policy_label("PHID-PROJ-infra", names) == "#infrastructure"

    def test_a_user_is_named_by_username(self):
        phab = _phab(
            phids={
                "PHID-USER-admin": {
                    "type": "USER",
                    "name": "admin",
                    "fullName": "admin (Administrator)",
                    "uri": "http://phorge.localhost/p/admin/",
                }
            }
        )

        names = resolve_policy_names(phab, ["PHID-USER-admin"])

        assert names == {"PHID-USER-admin": "@admin"}

    def test_anything_else_is_whatever_the_instance_calls_it(self):
        """A custom policy rule, most often."""
        phab = _phab(phids={"PHID-PLCY-x": {"type": "PLCY", "name": "Custom Policy"}})

        assert resolve_policy_names(phab, ["PHID-PLCY-x"]) == {
            "PHID-PLCY-x": "Custom Policy"
        }

    def test_keywords_alone_cost_no_round_trip(self):
        phab = _phab()

        assert resolve_policy_names(phab, ["public", "users", None]) == {}
        phab.phid.query.assert_not_called()

    def test_each_phid_is_asked_about_once(self):
        phab = _phab()

        resolve_policy_names(phab, ["PHID-PROJ-a", "PHID-PROJ-a", "PHID-PROJ-b"])

        phab.phid.query.assert_called_once_with(phids=["PHID-PROJ-a", "PHID-PROJ-b"])

    def test_a_phid_the_instance_would_not_name_is_shown_as_it_stands(self):
        """Inventing a name for a policy is worse than showing the PHID."""
        assert policy_label("PHID-PROJ-secret", {}) == "PHID-PROJ-secret"

    def test_naming_never_fails_a_read(self):
        phab = _phab()
        phab.phid.query.side_effect = RuntimeError("boom")

        assert resolve_policy_names(phab, ["PHID-PROJ-a"]) == {}

    def test_many_phids_are_asked_about_in_chunks(self):
        """An audit of every project can carry hundreds of policy PHIDs.

        They are named a chunk at a time rather than in one request, and
        every one of them still comes back named.
        """
        phids = [f"PHID-PROJ-{n:04d}" for n in range(PHID_QUERY_CHUNK * 2 + 1)]
        phab = MagicMock()
        phab.phid.query.side_effect = lambda phids: _Result(
            {
                phid: {"type": "PROJ", "uri": f"http://x/tag/{phid[-4:]}/"}
                for phid in phids
            }
        )

        names = resolve_policy_names(phab, phids)

        assert phab.phid.query.call_count == 3
        assert all(
            len(call.kwargs["phids"]) <= PHID_QUERY_CHUNK
            for call in phab.phid.query.call_args_list
        )
        assert len(names) == len(phids)

    def test_a_failed_chunk_leaves_only_its_own_phids_unnamed(self):
        phids = [f"PHID-PROJ-{n:04d}" for n in range(PHID_QUERY_CHUNK + 1)]
        phab = MagicMock()
        phab.phid.query.side_effect = [
            RuntimeError("boom"),
            _Result({phids[-1]: {"type": "PROJ", "uri": "http://x/tag/last/"}}),
        ]

        assert resolve_policy_names(phab, phids) == {phids[-1]: "#last"}


class TestLockout:
    """Phorge will not let you apply a policy that takes the object away."""

    def test_the_validation_error_becomes_a_sentence(self):
        message = policy_lockout_message(
            "ERROR-CONDUIT-CORE: Validation errors:\n"
            "  - The view policy of this object would no longer allow you to "
            "view the object."
        )

        assert message.startswith("The view policy of this object")
        assert "Nothing was changed" in message
        assert "Validation errors" not in message

    def test_any_other_error_is_left_to_the_caller(self):
        assert policy_lockout_message("ERR-CONDUIT-CORE: no such repository") is None

    def test_an_exception_is_read_as_readily_as_a_string(self):
        error = Exception("would no longer allow you to edit the object.")

        assert "Nothing was changed" in policy_lockout_message(error)
