# -*- coding: utf-8 -*-

# python stdlib
import logging
import logging.config
import sys

__author__ = "Rickard Eriksson"
__email__ = "rickard@dynamist.se"
__version__ = "0.4.0"
__url__ = "https://github.com/dynamist/phabfive"


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
