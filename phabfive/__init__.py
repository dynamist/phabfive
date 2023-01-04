# -*- coding: utf-8 -*-

# python stdlib
import logging
import os
import sys

__author__ = "Rickard Eriksson"
__email__ = "rickard@dynamist.se"
__version__ = "0.2.0"
__url__ = "https://github.com/dynamist/phabfive"


def init_logging(log_level):
    """
    Init logging settings with default set to INFO
    """
    _log_level = logging.getLevelName(log_level)

    if isinstance(_log_level, str):
        print("CRITICAL: Undefined log-level set, please use any of the defined log levels inside Python logging module")
        sys.exit(1)

    if log_level == "DEBUG":
        msg = "%(levelname)s - %(name)s:%(lineno)s - %(message)s"
    else:
        msg = "%(levelname)s - %(message)s"

    logging_conf = {
        "version": 1,
        "root": {
            "level": log_level,
            "handlers": ["console"],
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            },
        },
        "formatters": {
            "simple": {
                "format": f"{msg}",
            },
        },
    }

    logging.config.dictConfig(logging_conf)
