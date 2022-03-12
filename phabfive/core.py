# -*- coding: utf-8 -*-

from __future__ import absolute_import

# python std lib
import copy
import logging
import os
import re

# phabfive imports
from phabfive.exceptions import PhabfiveConfigException, PhabfiveRemoteException


# 3rd party imports
import anyconfig
import appdirs
from phabricator import Phabricator, APIError


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
logging.getLogger("anyconfig").setLevel(logging.ERROR)


CONFIGURABLES = ["PHABFIVE_DEBUG", "PHAB_TOKEN", "PHAB_URL"]
DEFAULTS = {"PHABFIVE_DEBUG": False, "PHAB_TOKEN": "", "PHAB_URL": ""}
REQUIRED = ["PHAB_TOKEN", "PHAB_URL"]
VALIDATORS = {
    "PHAB_URL": "^http(s)?://[a-zA-Z0-9._-]+/api/$",
    "PHAB_TOKEN": "^[a-zA-Z0-9-]{32}$",
}
VALID_EXAMPLES = {"PHAB_URL": "example: http://127.0.0.1/api/"}
CONFIG_EXAMPLES = {
    "PHAB_TOKEN": "example: export PHAB_TOKEN=cli-RANDOMRANDOMRANDOMRANDOMRAND",
    "PHAB_URL": "example: echo PHAB_URL: https://dynamist.phacility.com/api/ >> ~/.config/phabfive.yaml",
}


class Phabfive(object):
    def __init__(self):

        # Get super-early debugging by `export PHABFIVE_DEBUG=1`
        if "PHABFIVE_DEBUG" in os.environ:
            log.setLevel(logging.DEBUG)
            log.info(
                "Loglevel is: {}".format(logging.getLevelName(log.getEffectiveLevel()))
            )

        self.conf = self.load_config()

        maxlen = 8 + len(max(dict(self.conf).keys(), key=len))
        for k, v in dict(self.conf).items():
            log.debug("{} {} {}".format(k, "." * (maxlen - len(k)), v))

        # check for required configurables
        for k, v in dict(self.conf).items():
            if k in REQUIRED and not v:
                error = "{} is not configured".format(k)
                example = CONFIG_EXAMPLES.get(k)
                if example:
                    error += ", " + example
                raise PhabfiveConfigException(error)

        # check validity of configurables
        for k in VALIDATORS.keys():
            if not re.match(VALIDATORS[k], self.conf[k]):
                error = "{} is malformed".format(k)
                example = VALID_EXAMPLES.get(k)
                if example:
                    error += ", " + example
                raise PhabfiveConfigException(error)
        self.phab = Phabricator(
            host=self.conf.get("PHAB_URL"), token=self.conf.get("PHAB_TOKEN")
        )

        self.verify_connection()

    def verify_connection(self):
        """ """
        try:
            self.phab.user.whoami()
        except APIError as e:
            raise PhabfiveRemoteException(e)

    def load_config(self):
        """
        Load configuration from configuration files and environment variables.

        Search order, latest has presedence:

          1. hard coded defaults
          2. `/etc/phabfive.yaml`
          3. `/etc/phabfive.d/*.yaml`
          4. `~/.config/phabfive.yaml`
          5. `~/.config/phabfive.d/*.yaml`
          6. environment variables
        """
        environ = os.environ.copy()

        log.debug("Loading configuration defaults")
        conf = copy.deepcopy(DEFAULTS)

        os.environ["XDG_CONFIG_DIRS"] = "/etc"

        site_conf_file = os.path.join(appdirs.site_config_dir("phabfive") + ".yaml")
        log.debug("Loading configuration file: {}".format(site_conf_file))
        anyconfig.merge(
            conf,
            {
                k: v
                for k, v in dict(
                    anyconfig.load(site_conf_file, ac_ignore_missing=True)
                ).items()
                if k in CONFIGURABLES
            },
        )

        site_conf_dir = os.path.join(
            appdirs.site_config_dir("phabfive") + ".d", "*.yaml"
        )
        log.debug("Loading configuration files: {}".format(site_conf_dir))
        anyconfig.merge(
            conf,
            {
                k: v
                for k, v in dict(anyconfig.multi_load(site_conf_dir)).items()
                if k in CONFIGURABLES
            },
        )

        user_conf_file = os.path.join(appdirs.user_config_dir("phabfive")) + ".yaml"
        log.debug("Loading configuration file: {}".format(user_conf_file))
        anyconfig.merge(
            conf,
            {
                k: v
                for k, v in dict(
                    anyconfig.load(user_conf_file, ac_ignore_missing=True)
                ).items()
                if k in CONFIGURABLES
            },
        )

        user_conf_dir = os.path.join(
            appdirs.user_config_dir("phabfive") + ".d", "*.yaml"
        )
        log.debug("Loading configuration files: {}".format(user_conf_dir))
        anyconfig.merge(
            conf,
            {
                k: v
                for k, v in dict(anyconfig.multi_load(user_conf_dir)).items()
                if k in CONFIGURABLES
            },
        )

        log.debug("Loading configuration from environment")
        anyconfig.merge(conf, {k: v for k, v in environ.items() if k in CONFIGURABLES})

        return conf
