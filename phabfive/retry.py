# -*- coding: utf-8 -*-
"""When a Conduit call is tried again, and how long phabfive waits first.

The `phabricator` library mounts its own retry on every request: three
immediate attempts, with no backoff, no retry on 429 or 5xx, and - since
Conduit is always POST - a read timeout retried even for a write. A slow
`maniphest.edit` carrying a comment could be sent twice.

`RetryPolicy` replaces it. phabfive.conduit mounts the policy on every call
it makes, which is what makes it the one policy phabfive has:

* connection errors, timeouts, 429 and 5xx are retried, with exponential
  backoff and full jitter, and a `Retry-After` is honoured
* no wait is ever longer than PHAB_BACKOFF_MAX, `Retry-After` included
* a read - `*.search`, `*.query`, `phid.lookup`, `user.whoami` - is retried
  on any of those
* a write is retried only on a connection that was never made, which cannot
  have reached the server, unless the call is made inside
  `idempotent_writes()`. Setting a policy is idempotent; posting a comment is
  not

A call that succeeds costs nothing extra, so the interactive case is never
slower. Only a failing one waits, and the defaults keep that under two
seconds in total.

`Pacer` is the other half of being kind to a struggling server: an optional
pause between the writes of a batch, PHAB_PACE seconds.
"""

import contextlib
import contextvars
import logging
import math
import random
import time
from collections.abc import Mapping
from typing import cast

from phabfive.constants import DEFAULTS
from phabfive.exceptions import PhabfiveConfigException

log = logging.getLogger(__name__)

#: How often a failed call is tried again, and the longest single wait in
#: seconds. Small, because a person is usually waiting: a bulk job that
#: should ride out a restart raises both, e.g. PHAB_RETRY=8
#: PHAB_BACKOFF_MAX=60.
# DEFAULTS mixes value types, so mypy sees each entry as `object`.
DEFAULT_RETRIES = cast(int, DEFAULTS["PHAB_RETRY"])
DEFAULT_BACKOFF_MAX = float(cast(int, DEFAULTS["PHAB_BACKOFF_MAX"]))

#: The first wait before jitter; each retry doubles it.
BACKOFF_BASE = 0.25

#: The statuses worth waiting out. A 4xx other than 429 is an answer, and
#: asking again gets the same one.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Conduit method names that only read, matched on the last dotted part:
#: `maniphest.search`, `diffusion.branchquery`, `maniphest.querystatuses`,
#: `maniphest.gettasktransactions`.
_READ_SUFFIXES = ("search", "query")
_READ_PREFIXES = ("query", "get")
_READ_NAMES = frozenset({"whoami", "info", "lookup", "ping"})

#: Transaction types that set a value, so that applying one twice leaves the
#: object as applying it once does. A type missing from here is treated as
#: not idempotent, which only costs a retry; one wrongly present could
#: duplicate a write.
IDEMPOTENT_TRANSACTIONS = frozenset(
    {
        "title",
        "description",
        "priority",
        "status",
        "owner",
        "space",
        "column",
        "view",
        "edit",
        "projects.set",
        "projects.add",
        "projects.remove",
        "subscribers.set",
        "subscribers.add",
        "subscribers.remove",
    }
)

_idempotent = contextvars.ContextVar("phabfive_idempotent_writes", default=False)


def is_read(method):
    """Whether a Conduit method only reads, e.g. "maniphest.search"."""
    name = method.rsplit(".", 1)[-1].lower()
    return (
        name.endswith(_READ_SUFFIXES)
        or name.startswith(_READ_PREFIXES)
        or name in _READ_NAMES
    )


def is_idempotent_edit(transactions):
    """Whether sending these transactions to an existing object twice is safe."""
    return bool(transactions) and all(
        t.get("type") in IDEMPOTENT_TRANSACTIONS for t in transactions
    )


@contextlib.contextmanager
def idempotent_writes(enabled=True):
    """Let the writes made inside be retried like reads.

    Only for a call whose second arrival changes nothing the first did not:
    an edit that sets fields of an existing object. Never for a create or a
    comment. `enabled=False` makes the block an ordinary one, so a call site
    can decide per call.
    """
    token = _idempotent.set(bool(enabled))
    try:
        yield
    finally:
        _idempotent.reset(token)


def retries_writes():
    """Whether the current block was marked by `idempotent_writes()`."""
    return _idempotent.get()


def _sleep(seconds):
    """The one place phabfive waits, so a test can replace it."""
    time.sleep(seconds)


def _number(conf, key, default, kind):
    """A finite, non-negative number from the configuration, or `default`.

    "nan" and "inf" parse as floats but are refused: a nan wait never waits,
    and an infinite cap is no cap. Anything that is not a mapping - no configuration at all, or a test's
    stand-in for an app - reads as every key unset.
    """
    value = conf.get(key) if isinstance(conf, Mapping) else None
    if value is None or str(value).strip() == "":
        return default
    try:
        number = kind(str(value).strip())
    except ValueError:
        number = -1
    if not math.isfinite(number) or number < 0:
        raise PhabfiveConfigException(
            f"{key} must be a number of {'retries' if kind is int else 'seconds'}"
            f" of at least 0, not {value!r}"
        )
    return number


class RetryPolicy:
    """How often, and how patiently, a failed Conduit call is tried again.

    Parameters
    ----------
    retries : int
        Attempts after the first. 0 turns retrying off entirely.
    backoff_max : float
        The longest single wait, in seconds. It caps `Retry-After` too, so
        nothing phabfive does ever waits longer than this in one go.
    """

    __slots__ = ("retries", "backoff_max")

    def __init__(self, retries=DEFAULT_RETRIES, backoff_max=DEFAULT_BACKOFF_MAX):
        self.retries = retries
        self.backoff_max = backoff_max

    @classmethod
    def from_conf(cls, conf):
        """The policy PHAB_RETRY and PHAB_BACKOFF_MAX describe.

        Raises
        ------
        PhabfiveConfigException
            For a value that is not a non-negative number.
        """
        return cls(
            retries=_number(conf, "PHAB_RETRY", DEFAULT_RETRIES, int),
            backoff_max=_number(conf, "PHAB_BACKOFF_MAX", DEFAULT_BACKOFF_MAX, float),
        )

    def backoff(self, attempt):
        """The wait before retry number `attempt` (1-based), jitter applied.

        Full jitter - anywhere between nothing and the exponential ceiling -
        so that many clients failing together do not come back together.
        """
        ceiling = min(self.backoff_max, BACKOFF_BASE * 2 ** (attempt - 1))
        return random.uniform(0, ceiling)

    def urllib3_retry(self, method):
        """The `urllib3.Retry` to send a call to `method` with.

        urllib3 already knows which failures are safe to repeat for a POST:
        a connect error is retried whatever the method, since nothing was
        sent, while a read error or a retryable status is retried only for a
        method in `allowed_methods`. So a read, or a write inside
        `idempotent_writes()`, allows POST, and any other write allows
        nothing.
        """
        safe = is_read(method) or retries_writes()
        return _retry_class()(
            total=self.retries,
            connect=self.retries,
            read=self.retries if safe else 0,
            status=self.retries if safe else 0,
            other=0,
            allowed_methods=frozenset({"POST"}) if safe else frozenset(),
            status_forcelist=RETRY_STATUSES,
            # The last response is handed back, and the client reports its
            # status, rather than urllib3 raising MaxRetryError about it.
            raise_on_status=False,
            respect_retry_after_header=True,
            backoff_factor=0,
            policy=self,
            method_name=method,
        )

    def __repr__(self):
        return f"RetryPolicy(retries={self.retries}, backoff_max={self.backoff_max})"


_RETRY_CLASS = None


def _retry_class():
    """A `urllib3.Retry` that waits the way `RetryPolicy` says.

    Built on first use, so importing this module does not import urllib3.
    """
    global _RETRY_CLASS
    if _RETRY_CLASS is not None:
        return _RETRY_CLASS

    from urllib3.exceptions import ConnectTimeoutError, TimeoutError
    from urllib3.util.retry import Retry

    def _reason(last):
        """What went wrong with an attempt, in a few words."""
        if last is None:
            return "failed"
        if last.status:
            return f"HTTP {last.status}"
        if isinstance(last.error, ConnectTimeoutError):
            return "could not connect"
        if isinstance(last.error, TimeoutError):
            return "timed out"
        return "connection broken"

    class PolicyRetry(Retry):
        """Retry whose waits come from a RetryPolicy, and are logged."""

        def __init__(self, *args, policy=None, method_name="", **kwargs):
            super().__init__(*args, **kwargs)
            self.policy = policy or RetryPolicy()
            self.method_name = method_name

        def new(self, **kw):
            # urllib3 rebuilds the object after every attempt from its own
            # parameters, which do not include ours.
            kw.setdefault("policy", self.policy)
            kw.setdefault("method_name", self.method_name)
            return super().new(**kw)

        def sleep(self, response=None):
            attempt = len(self.history)
            delay = None
            if response is not None:
                delay = self.get_retry_after(response)
            if delay is None:
                delay = self.policy.backoff(attempt)
            delay = min(delay, self.policy.backoff_max)

            reason = _reason(self.history[-1] if self.history else None)
            log.warning(
                f"{self.method_name}: {reason}, retry {attempt} of "
                f"{self.policy.retries} in {delay:.1f}s"
            )
            if delay > 0:
                _sleep(delay)

    _RETRY_CLASS = PolicyRetry
    return _RETRY_CLASS


class Pacer:
    """Keep at least `seconds` between one write and the next.

    `wait()` before each write. The first never waits, and time already
    spent since the previous write - reviewing, or fetching - counts.
    """

    __slots__ = ("seconds", "_last")

    def __init__(self, seconds=0.0):
        self.seconds = seconds
        self._last = None

    @classmethod
    def from_conf(cls, conf):
        """The pace PHAB_PACE describes, 0 (no pause) by default."""
        return cls(_number(conf, "PHAB_PACE", 0.0, float))

    def wait(self):
        if self.seconds and self._last is not None:
            remaining = self.seconds - (time.monotonic() - self._last)
            if remaining > 0:
                _sleep(remaining)
        self._last = time.monotonic()


__all__ = [
    "IDEMPOTENT_TRANSACTIONS",
    "Pacer",
    "RetryPolicy",
    "idempotent_writes",
    "is_idempotent_edit",
    "is_read",
]
