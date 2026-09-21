"""phabfive.conduit.Conduit builds its client on first use, and only then."""

from unittest.mock import MagicMock

from phabfive.conduit import Conduit


def _counting_factory():
    """A factory that records how often it was asked for a client."""
    client = MagicMock()
    calls = []

    def factory():
        calls.append(1)
        return client

    return factory, client, calls


class TestLaziness:
    def test_building_the_wrapper_makes_no_client(self):
        factory, _client, calls = _counting_factory()

        conduit = Conduit(factory)

        assert calls == []
        assert not conduit.is_built

    def test_first_use_builds_and_loads_interfaces(self):
        factory, client, calls = _counting_factory()
        conduit = Conduit(factory)

        conduit.user.whoami()

        assert calls == [1]
        assert conduit.is_built
        client.update_interfaces.assert_called_once_with()
        client.user.whoami.assert_called_once_with()

    def test_client_is_built_once(self):
        factory, client, calls = _counting_factory()
        conduit = Conduit(factory)

        conduit.maniphest.search()
        conduit.project.search()
        conduit.client

        assert calls == [1]
        client.update_interfaces.assert_called_once_with()

    def test_repr_does_not_build(self):
        factory, _client, calls = _counting_factory()
        conduit = Conduit(factory)

        assert repr(conduit) == "<Conduit (not built)>"
        assert calls == []


class TestPassThrough:
    def test_reads_reach_the_client(self):
        factory, client, _calls = _counting_factory()
        client.maniphest.search.return_value = {"data": []}

        assert Conduit(factory).maniphest.search() == {"data": []}

    def test_writes_reach_the_client(self):
        """`phab.user = mock` must replace the client's, not shadow it here."""
        factory, client, _calls = _counting_factory()
        conduit = Conduit(factory)
        replacement = MagicMock()

        conduit.user = replacement

        assert client.user is replacement
        assert conduit.user is replacement

    def test_client_property_is_the_real_client(self):
        factory, client, _calls = _counting_factory()

        assert Conduit(factory).client is client
