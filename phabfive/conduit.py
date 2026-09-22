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

And it is where phabfive.retry's policy meets the wire. The library mounts
its own retry on every request - immediate, and willing to send a write
twice after a read timeout - so each call made through here mounts one built
from the instance's `RetryPolicy` in its place first.
"""

import contextlib
import re

from phabfive.exceptions import PhabfiveAPIException, PhabfiveConnectionException
from phabfive.retry import RetryPolicy, is_read, retries_writes


@contextlib.contextmanager
def translated_errors(write=None):
    """Raise the client's exceptions as phabfive's, chained to the original.

    `write` names a call that the retry policy would not repeat. When such a
    call was sent and its answer never came, the server may well have
    applied it, and the error says so rather than reading as a refused
    connection.
    """
    import requests
    from phabricator import APIError

    try:
        yield
    except APIError as e:
        raise PhabfiveAPIException(e.code, e.message) from e
    except requests.exceptions.RequestException as e:
        if write and _unanswered(e):
            raise PhabfiveConnectionException(
                f"{write} was sent but no answer came back, so it may have been "
                "applied. It was not sent again, since doing so is not safe: "
                "check before trying again"
            ) from e
        status = _server_error(e) if write else None
        if status:
            # A 502 or 504 from a proxy usually means the backend carried on
            # working, and a 500 may come after part of the work was done.
            raise PhabfiveConnectionException(
                f"{write} was sent and the server answered HTTP {status}, so it "
                "may have been applied. It was not sent again, since doing so "
                "is not safe: check before trying again"
            ) from e
        raise PhabfiveConnectionException(str(e)) from e


def _unanswered(error):
    """Whether a request reached the server and then lost its answer."""
    from urllib3.exceptions import MaxRetryError, ProtocolError, ReadTimeoutError

    cause = error.args[0] if error.args else None
    if isinstance(cause, MaxRetryError):
        cause = cause.reason
    return isinstance(cause, (ReadTimeoutError, ProtocolError))


#: How the `phabricator` library reports a status other than 2xx. It raises
#: `requests.HTTPError` with no response attached, so the message is all
#: there is to go on.
_BAD_STATUS = re.compile(r"Bad response status: (5\d\d)\b")


def _server_error(error):
    """The 5xx status a request was answered with, or None."""
    import requests

    if not isinstance(error, requests.exceptions.HTTPError):
        return None
    match = _BAD_STATUS.search(str(error))
    return int(match.group(1)) if match else None


def _wrap(value, retry):
    """Wrap a Conduit method, and leave everything else as it is.

    Only `phabricator.Resource` objects are wrapped - the endpoints and
    methods of a real client. A test's MagicMock client passes through
    untouched, so `phab.maniphest.search.call_count` is still an int and
    `.call_args` still compares equal to `call(...)`.
    """
    from phabricator import Resource

    return Endpoint(value, retry) if isinstance(value, Resource) else value


def _mount_retry(resource, method, retry):
    """Send `resource`'s next call with `retry` instead of the library's.

    The library gives every Resource a session of its own, so this is done
    per Resource. A read keeps the adapter it was given, and with it the
    pooled keep-alive connection, for as long as the settings match - which
    is what lets every page of a long search share one connection, as they
    did under the library's adapter. A write that would not be retried gets
    a fresh adapter each call, so it is never sent on a pooled connection
    the server has since closed: that failure would read as a write that
    may have been applied. A replaced adapter is closed, not left to hold
    its connections open.
    """
    from requests.adapters import HTTPAdapter

    safe = is_read(method) or retries_writes()
    key = (retry, safe)
    if safe and getattr(resource, "_phabfive_retry_key", None) == key:
        return

    replaced = resource.session.adapters.get("https://")
    adapter = HTTPAdapter(max_retries=retry.urllib3_retry(method))
    resource.session.mount("https://", adapter)
    resource.session.mount("http://", adapter)
    resource._phabfive_retry_key = key
    if replaced is not None:
        replaced.close()


class Endpoint:
    """A Conduit endpoint or method whose calls raise phabfive's errors."""

    __slots__ = ("_resource", "_retry")

    def __init__(self, resource, retry=None):
        object.__setattr__(self, "_resource", resource)
        object.__setattr__(self, "_retry", retry or RetryPolicy())

    def __getattr__(self, name):
        return _wrap(
            getattr(object.__getattribute__(self, "_resource"), name),
            object.__getattribute__(self, "_retry"),
        )

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_resource"), name, value)

    def __call__(self, **kwargs):
        resource = object.__getattribute__(self, "_resource")
        method = f"{resource.method}.{resource.endpoint}"
        _mount_retry(resource, method, object.__getattribute__(self, "_retry"))
        unsafe = not (is_read(method) or retries_writes())
        with translated_errors(write=method if unsafe else None):
            return resource(**kwargs)

    def __repr__(self):
        resource = object.__getattribute__(self, "_resource")
        return f"<Endpoint {resource.endpoint}.{resource.method}>"


def _load_interfaces(client, retry):
    """Do what `client.update_interfaces()` does, through `retry`.

    The library's own method builds its `conduit.query` Resource inside
    itself, out of reach of `_mount_retry`, so for a real client the same
    two steps are taken here instead. Anything else - a test's MagicMock -
    is asked to update itself, as before.
    """
    from phabricator import Phabricator, Resource, parse_interfaces

    if not isinstance(client, Phabricator):
        with translated_errors():
            client.update_interfaces()
        return

    query = Endpoint(Resource(api=client, method="conduit", endpoint="query"), retry)
    client._interface = parse_interfaces(query())


class Conduit:
    """A lazily built Conduit client.

    Parameters
    ----------
    factory : callable
        Takes no arguments and returns an unconnected client, typically a
        `phabricator.Phabricator`. Called at most once.
    retry : RetryPolicy, optional
        How a failed call is tried again. The defaults when omitted.
    """

    __slots__ = ("_factory", "_client", "_retry")

    def __init__(self, factory, retry=None):
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_client", None)
        object.__setattr__(self, "_retry", retry or RetryPolicy())

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
            _load_interfaces(client, object.__getattribute__(self, "_retry"))
            object.__setattr__(self, "_client", client)
        return client

    @property
    def is_built(self):
        """Whether the client exists yet, so whether any request was made."""
        return object.__getattribute__(self, "_client") is not None

    def __getattr__(self, name):
        return _wrap(
            getattr(self.client, name), object.__getattribute__(self, "_retry")
        )

    def __setattr__(self, name, value):
        setattr(self.client, name, value)

    def __repr__(self):
        state = "built" if self.is_built else "not built"
        return f"<Conduit ({state})>"


__all__ = ["Conduit", "Endpoint", "translated_errors"]
