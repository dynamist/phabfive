# -*- coding: utf-8 -*-

"""Finding out what a server's Conduit accepts, once per client."""

# python std lib
from unittest.mock import MagicMock

# 3rd party imports
import pytest

# phabfive imports
from phabfive.capabilities import (
    known_constraint,
    rejects_constraint,
    remember_constraint,
    supports_constraint,
    supports_method,
)
from phabfive.exceptions import PhabfiveAPIException, PhabfiveConnectionException

REFUSED = PhabfiveAPIException(
    "ERR-CONDUIT-CORE", 'Constraint "status" is not a valid constraint for this query.'
)


class TestRejectsConstraint:
    def test_the_refusal_of_that_constraint(self):
        assert rejects_constraint(REFUSED, "status")

    def test_not_the_refusal_of_another(self):
        assert not rejects_constraint(REFUSED, "statuses")

    def test_not_another_error(self):
        other = PhabfiveAPIException("ERR-CONDUIT-CORE", "Something else.")

        assert not rejects_constraint(other, "status")
        assert not rejects_constraint(PhabfiveConnectionException("down"), "status")


class TestSupportsConstraint:
    def test_a_constraint_the_server_has(self):
        phab = MagicMock()
        phab.project.search.return_value = {"data": []}

        assert supports_constraint(phab, "project.search", "status", "all")
        phab.project.search.assert_called_once_with(
            constraints={"status": "all"}, limit=1
        )

    def test_a_constraint_it_refuses_is_asked_once(self):
        phab = MagicMock()
        phab.diffusion.repository.search.side_effect = REFUSED

        for _ in range(2):
            assert not supports_constraint(
                phab, "diffusion.repository.search", "status", "active"
            )

        phab.diffusion.repository.search.assert_called_once()

    def test_any_other_failure_is_raised_and_not_remembered(self):
        phab = MagicMock()
        phab.project.search.side_effect = PhabfiveConnectionException("down")

        with pytest.raises(PhabfiveConnectionException):
            supports_constraint(phab, "project.search", "status", "all")

        assert known_constraint(phab, "project.search", "status") is None

    def test_what_a_search_learned_is_not_asked_again(self):
        phab = MagicMock()
        remember_constraint(phab, "project.search", "status", False)

        assert not supports_constraint(phab, "project.search", "status", "all")
        phab.project.search.assert_not_called()

    def test_each_client_learns_for_itself(self):
        phorge, phabricator = MagicMock(), MagicMock()
        remember_constraint(phabricator, "project.search", "status", False)

        assert known_constraint(phorge, "project.search", "status") is None


class TestSupportsMethod:
    def test_one_method_list_answers_every_method(self):
        phab = MagicMock()
        phab.conduit.query.return_value = {
            "conduit.query": {},
            "owners.query": {},
        }

        assert supports_method(phab, "owners.query")
        assert not supports_method(phab, "conpherence.search")
        phab.conduit.query.assert_called_once()


def test_an_app_asks_through_its_client():
    from unittest.mock import patch

    from phabfive.core import Phabfive

    with patch("phabfive.core.Phabfive.__init__", return_value=None):
        app = Phabfive()
    app.phab = MagicMock()
    app.phab.project.search.side_effect = REFUSED

    assert not app.supports_constraint("project.search", "status", "all")
