"""phabfive.conduit.Conduit builds its client on first use, and only then."""

from unittest.mock import MagicMock, patch

import pytest

from phabfive.conduit import Conduit
from phabfive.exceptions import PhabfiveAPIException, PhabfiveConnectionException


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


class TestErrorTranslation:
    """A real client's errors come out as phabfive's, chained to the original."""

    @pytest.fixture
    def conduit(self):
        from phabricator import Phabricator

        return Conduit(
            lambda: Phabricator(host="https://phorge.example.com/api/", token="t" * 32)
        )

    def _raising(self, error):
        """Replace every Conduit request, update_interfaces' included."""
        return patch("phabricator.Resource._request", side_effect=error)

    def test_an_api_error_on_a_call(self, conduit):
        from phabricator import APIError

        with patch("phabfive.conduit._load_interfaces"):
            conduit.client
        with self._raising(APIError("ERR-CONDUIT-CORE", "No such thing.")):
            with pytest.raises(PhabfiveAPIException) as caught:
                conduit.maniphest.search()

        assert caught.value.code == "ERR-CONDUIT-CORE"
        assert str(caught.value) == "ERR-CONDUIT-CORE: No such thing."
        assert isinstance(caught.value.__cause__, APIError)

    def test_an_api_error_loading_interfaces(self, conduit):
        """The first request of all loads the interfaces; it must translate too."""
        from phabricator import APIError

        with self._raising(APIError("ERR-INVALID-AUTH", "API token is not valid.")):
            with pytest.raises(PhabfiveAPIException, match="ERR-INVALID-AUTH"):
                conduit.user.whoami()

    def test_a_connection_error(self, conduit):
        import requests

        refused = requests.exceptions.ConnectionError("Connection refused")
        with self._raising(refused):
            with pytest.raises(PhabfiveConnectionException) as caught:
                conduit.user.whoami()

        assert str(caught.value) == "Connection refused"
        assert caught.value.__cause__ is refused

    def test_nested_endpoints_translate(self, conduit):
        from phabricator import APIError

        with patch("phabfive.conduit._load_interfaces"):
            conduit.client
        with self._raising(APIError("ERR-X", "nested")):
            with pytest.raises(PhabfiveAPIException):
                conduit.project.column.search()

    def test_a_result_passes_through(self, conduit):
        with patch("phabfive.conduit._load_interfaces"):
            conduit.client
        with patch("phabricator.Resource._request", return_value={"data": []}):
            assert conduit.maniphest.search() == {"data": []}


def test_only_conduit_handles_the_clients_exceptions():
    """A module catching APIError itself would catch nothing any more.

    Conduit translates before any other code sees the error, so an `except
    APIError` or `except requests...` anywhere else is silently dead. Only
    conduit.py may import requests or name APIError.
    """
    import ast
    from pathlib import Path

    import phabfive

    offenders = []
    for path in sorted(Path(phabfive.__file__).parent.rglob("*.py")):
        if path.name == "conduit.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            else:
                continue
            if any(n == "requests" or n.startswith("requests.") for n in names):
                offenders.append(f"{path.name}: imports requests")
            if "APIError" in names:
                offenders.append(f"{path.name}: imports APIError")
    assert offenders == []
