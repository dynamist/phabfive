# -*- coding: utf-8 -*-

# python std lib
import copy
import logging
import os
import re
from urllib.parse import urlparse

# phabfive imports
from phabfive.constants import *
from phabfive.exceptions import PhabfiveConfigException, PhabfiveRemoteException

# 3rd party imports
import anyconfig
import appdirs
from phabricator import Phabricator, APIError


log = logging.getLogger(__name__)
logging.getLogger("anyconfig").setLevel(logging.ERROR)


class Phabfive():

    def __init__(self):
        """
        """
        self.conf = self.load_config()

        maxlen = 8 + len(max(dict(self.conf).keys(), key=len))

        for key, value in dict(self.conf).items():
            dots = "." * (maxlen - len(key))
            log.debug(f"{key} {dots} {value}")

        # check for required configurables
        for conf_key, conf_value in dict(self.conf).items():
            if conf_key in REQUIRED and not conf_value:
                error = f"{conf_key} is not configured"
                example = CONFIG_EXAMPLES.get(conf_key)

                if example:
                    error += ", " + example

                raise PhabfiveConfigException(error)

        # check validity of configurables
        for validator_key in VALIDATORS.keys():
            if not re.match(VALIDATORS[validator_key], self.conf[validator_key]):
                error = f"{validator_key} is malformed"
                example = VALID_EXAMPLES.get(validator_key)

                if example:
                    error += ", " + example

                raise PhabfiveConfigException(error)

        self.phab = Phabricator(
            host=self.conf.get("PHAB_URL"),
            token=self.conf.get("PHAB_TOKEN"),
        )
        # This enables extra endpoints that normally is unaccessible
        self.phab.update_interfaces()

        url = urlparse(self.conf['PHAB_URL'])
        self.url = f"{url.scheme}://{url.netloc}"

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

        site_conf_file = os.path.join(f"{appdirs.site_config_dir('phabfive')}.yaml")
        log.debug(f"Loading configuration file: {site_conf_file}")
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(
                    anyconfig.load(
                        site_conf_file,
                        ac_ignore_missing=True,
                    )
                ).items()
                if key in CONFIGURABLES
            },
        )

        site_conf_dir = os.path.join(
            appdirs.site_config_dir("phabfive") + ".d", "*.yaml"
        )
        log.debug(f"Loading configuration files: {site_conf_dir}")
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(anyconfig.multi_load(site_conf_dir)).items()
                if key in CONFIGURABLES
            },
        )

        user_conf_file = os.path.join(f"{appdirs.user_config_dir('phabfive')}.yaml")
        log.debug(f"Loading configuration file: {user_conf_file}")
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(
                    anyconfig.load(
                        user_conf_file,
                        ac_ignore_missing=True,
                    )
                ).items()
                if key in CONFIGURABLES
            },
        )

        user_conf_dir = os.path.join(
            f"{appdirs.user_config_dir('phabfive')}.d", "*.yaml"
        )
        log.debug(f"Loading configuration files: {user_conf_dir}")
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in dict(anyconfig.multi_load(user_conf_dir)).items()
                if key in CONFIGURABLES
            },
        )

        log.debug("Loading configuration from environment variables")
        anyconfig.merge(
            conf,
            {
                key: value
                for key, value in environ.items()
                if key in CONFIGURABLES
            },
        )

        return conf

    def to_transactions(self, data):
        """
        Converts a dict of key:value pairs into a list of valid transaction objects
        that phabricator will accept when calling endpoints like edit
        """
        result = []

        for transaction_type, transaction_value in data.items():
            result.append({
                "type": transaction_type,
                "value": transaction_value,
            })

        return result
