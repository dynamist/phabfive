# -*- coding: utf-8 -*-
"""A Conduit client that is not built until it is used.

`Phabfive.__init__` used to build a `phabricator.Phabricator` and call
`update_interfaces()` on it straight away - a round trip for the method list,
paid by every instance whether it went on to make a call or not, and paid
again by every sibling instance an app built for itself. `Conduit` holds the
recipe instead, and makes the client, interfaces and all, the first time
anything reaches through it.

It stands in for the client everywhere `self.phab` is used, so attribute
reads and writes both pass straight through: `phab.maniphest.search(...)`
reads, and a test's `phab.user = MagicMock()` writes to the real client
rather than to this wrapper.
"""


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
            client.update_interfaces()
            object.__setattr__(self, "_client", client)
        return client

    @property
    def is_built(self):
        """Whether the client exists yet, so whether any request was made."""
        return object.__getattribute__(self, "_client") is not None

    def __getattr__(self, name):
        return getattr(self.client, name)

    def __setattr__(self, name, value):
        setattr(self.client, name, value)

    def __repr__(self):
        state = "built" if self.is_built else "not built"
        return f"<Conduit ({state})>"


__all__ = ["Conduit"]
