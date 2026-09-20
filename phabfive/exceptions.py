# -*- coding: utf-8 -*-


class PhabfiveException(Exception):
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


class PhabfiveRemoteException(PhabfiveException):
    pass


__all__ = [
    "PhabfiveException",
    "PhabfiveDataException",
    "PhabfiveNameCollisionException",
    "PhabfiveConfigException",
    "PhabfiveRemoteException",
]
