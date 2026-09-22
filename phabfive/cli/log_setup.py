# -*- coding: utf-8 -*-
"""Logging setup for the phabfive command.

Lives under cli/ because configuring the root logger is the application's
decision, never a library's: a program that imports phabfive keeps whatever
logging it already has.
"""

# python stdlib
import logging
import logging.config
import sys


def init_logging(log_level):
    """
    Init logging settings with default set to INFO
    """
    _log_level = logging.getLevelName(log_level)

    if isinstance(_log_level, str):
        print(
            "CRITICAL - Undefined log-level set, please use any of the defined log levels inside Python logging module",
            file=sys.stderr,
        )
        sys.exit(1)

    if log_level == "DEBUG":
        msg = "%(levelname)s - %(name)s:%(lineno)s - %(message)s"
    else:
        msg = "%(levelname)s - %(message)s"

    logging_conf = {
        "version": 1,
        "disable_existing_loggers": False,
        "root": {
            "level": log_level,
            "handlers": ["console"],
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "simple",
                "stream": sys.stderr,
            },
        },
        "formatters": {
            "simple": {
                "format": f"{msg}",
            },
        },
    }

    logging.config.dictConfig(logging_conf)

    # anyconfig narrates every file it looks for; the command has its own
    # debug output for that. Set here rather than when phabfive.core is
    # imported, because a library does not adjust other libraries' loggers.
    logging.getLogger("anyconfig").setLevel(logging.ERROR)

    # Each retry is announced by phabfive.retry, with the wait it chose.
    # urllib3 also warns about the connection errors among them, in its own
    # words and with a Retry repr, so outside debugging it keeps quiet.
    if log_level != "DEBUG":
        logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
