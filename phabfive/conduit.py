# -*- coding: utf-8 -*-
"""A Conduit client that is built on first use, and speaks phabfive's errors.

`Phabfive.__init__` used to build a `phabricator.Phabricator` and call
`update_interfaces()` on it straight away - a round trip for the method list,
paid by every instance whether it went on to make a call or not, and paid
again by every sibling instance an app built for itself. `Conduit` holds the
recipe instead, and makes the client, interfaces and all, the first time
anything reaches through it.

It is also the one place the client's own exceptions are translated.
`phabricator.APIError` becomes `PhabfiveAPIException` and
`requests.RequestException` becomes `PhabfiveConnectionException`, so no
caller - in phabfive or in a program using it - ever has to import either
library to catch what a call raises. Code under `phabfive/` catches the
phabfive types, never the originals.

It stands in for the client everywhere `self.phab` is used, so attribute
reads and writes both pass straight through: `phab.maniphest.search(...)`
reads, and a test's `phab.user = MagicMock()` writes to the real client
rather than to this wrapper.
"""

import contextlib

from phabfive.exceptions import PhabfiveAPIException, PhabfiveConnectionException


@contextlib.contextmanager
def translated_errors():
    """Raise the client's exceptions as phabfive's, chained to the original."""
    import requests
    from phabricator import APIError

    try:
        yield
    except APIError as e:
        raise PhabfiveAPIException(e.code, e.message) from e
    except requests.exceptions.RequestException as e:
        raise PhabfiveConnectionException(str(e)) from e


def _wrap(value):
    """Wrap a Conduit method, and leave everything else as it is.

    Only `phabricator.Resource` objects are wrapped - the endpoints and
    methods of a real client. A test's MagicMock client passes through
    untouched, so `phab.maniphest.search.call_count` is still an int and
    `.call_args` still compares equal to `call(...)`.
    """
    from phabricator import Resource

    return Endpoint(value) if isinstance(value, Resource) else value


class Endpoint:
    """A Conduit endpoint or method whose calls raise phabfive's errors."""

    __slots__ = ("_resource",)

    def __init__(self, resource):
        object.__setattr__(self, "_resource", resource)

    def __getattr__(self, name):
        return _wrap(getattr(object.__getattribute__(self, "_resource"), name))

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_resource"), name, value)

    def __call__(self, **kwargs):
        with translated_errors():
            return object.__getattribute__(self, "_resource")(**kwargs)

    def __repr__(self):
        resource = object.__getattribute__(self, "_resource")
        return f"<Endpoint {resource.endpoint}.{resource.method}>"


class Conduit:
    """A lazily built Conduit client.

    Parameters
    ----------
    factory : callable
        Takes no arguments and returns an unconnected client, typically a
        `phabricator.Phabricator`. Called at most once.
    """

    __slots__ = ("_factory", "_client")

    def __init__(self, factory):
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_client", None)

    @property
    def client(self):
        """The underlying client, built and its interfaces loaded on first use."""
        client = object.__getattribute__(self, "_client")
        if client is None:
            client = object.__getattribute__(self, "_factory")()
            # Loads the method list from the server, which is what makes
            # endpoints outside the library's bundled interface callable.
            # The first request any instance makes, so the first that can
            # fail - and it must fail in phabfive's terms like every other.
            with translated_errors():
                client.update_interfaces()
            object.__setattr__(self, "_client", client)
        return client

    @property
    def is_built(self):
        """Whether the client exists yet, so whether any request was made."""
        return object.__getattribute__(self, "_client") is not None

    def __getattr__(self, name):
        return _wrap(getattr(self.client, name))

    def __setattr__(self, name, value):
        setattr(self.client, name, value)

    def __repr__(self):
        state = "built" if self.is_built else "not built"
        return f"<Conduit ({state})>"


__all__ = ["Conduit", "Endpoint", "translated_errors"]
