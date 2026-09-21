# -*- coding: utf-8 -*-
"""Everything phabfive raises on purpose.

Standard library only, deliberately: catching phabfive's errors must not cost
a program the Conduit client and requests, and the phabricator package reads
~/.arcrc and ./.arcconfig as it imports. So nothing here subclasses
phabricator.APIError or requests.RequestException - phabfive.conduit
translates those into the Remote subclasses below instead.
"""


class PhabfiveException(Exception):
    """The base of everything phabfive raises on purpose."""

    pass


class PhabfiveDataException(PhabfiveException):
    """
    Raised when there are errors in the data being processed.

    Examples:
    - Invalid/malformed template files for task creation
    - Invalid identifiers (K123, P456, etc.)
    - Missing or unexpected data in API responses
    """

    pass


class PhabfiveNameCollisionException(PhabfiveDataException):
    """
    Raised when a name is not taken, but is close enough to one that is.

    A subclass of PhabfiveDataException so that every existing handler keeps
    catching it. It exists so a caller can tell "too similar to refuse
    silently" from "already exists", and offer the override only for the
    former - an exact clash has no override.
    """

    pass


class PhabfiveConfigException(PhabfiveException):
    """
    Raised when there are problems with configuration or command invocation.

    Examples:
    - Missing or malformed config file values
    - Invalid command-line arguments or options
    - Invalid option values that don't match allowed choices
    """

    pass


class PhabfiveInputException(PhabfiveConfigException, ValueError):
    """
    Raised when an argument's value is not one phabfive can use.

    A monogram that is not one, a transition pattern that does not parse, a
    priority that does not exist. A PhabfiveConfigException, whose handlers
    already answer "invalid command-line arguments", and a ValueError, which
    is what Python code expects a bad value to raise.
    """

    pass


class PhabfiveNotFoundException(PhabfiveDataException, LookupError):
    """
    Raised when something named does not exist, or is not visible.

    Phorge answers an object the viewer may not see exactly as one that does
    not exist, so the two cannot be told apart and are not.
    """

    pass


class PhabfiveRemoteException(PhabfiveException):
    """Raised when the server could not be asked, or refused what was asked."""

    pass


class PhabfiveAPIException(PhabfiveRemoteException):
    """
    Raised when Conduit answered a call with an error.

    Carries the same `code` and `message` as the phabricator.APIError it
    replaces, and prints the same way - `ERR-INVALID-AUTH: API token ...` - so
    a message built from one reads exactly as it did from the other.
    """

    def __init__(self, code, message):
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self):
        return f"{self.code}: {self.message}"


class PhabfiveConnectionException(PhabfiveRemoteException):
    """
    Raised when the server could not be reached or answered with an HTTP error.

    Replaces requests.RequestException, which is what the client raises, and
    keeps its message.
    """

    pass


__all__ = [
    "PhabfiveAPIException",
    "PhabfiveConfigException",
    "PhabfiveConnectionException",
    "PhabfiveDataException",
    "PhabfiveException",
    "PhabfiveInputException",
    "PhabfiveNameCollisionException",
    "PhabfiveNotFoundException",
    "PhabfiveRemoteException",
]
