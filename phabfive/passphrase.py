# -*- coding: utf-8 -*-

# python std lib
import json
import logging
import re

# phabfive imports
from phabfive.core import Phabfive
from phabfive.constants import MONOGRAMS
from phabfive.exceptions import PhabfiveDataException, PhabfiveRemoteException

# 3rd party imports
from phabricator import APIError


log = logging.getLogger(__name__)


class Passphrase(Phabfive):
    def __init__(self):
        super(Passphrase, self).__init__()

    def _validate_identifier(self, id_):
        return re.match("^" + MONOGRAMS["passphrase"] + "$", id_)

    def get_secret(self, ids):
        if not self._validate_identifier(ids):
            raise PhabfiveDataException('Identifier "{0}" is not valid.'.format(ids))

        ids = ids.replace("K", "")

        try:
            response = self.phab.passphrase.query(ids=[ids], needSecrets=1)
        except APIError as e:
            raise PhabfiveRemoteException(e)

        has_data = response.get("data", {})

        if not has_data:
            raise PhabfiveDataException("K{0} has no data or other error.".format(ids))
        else:
            # TODO: I am doing the logging wrong, in this module the loglevel
            # is INFO, even if env PHABFIVE_DEBUG=1
            log.debug(json.dumps(response["data"], indent=2))

        # When Conduit Access is not accepted for Passphrase the "response" will return value "noAPIAccess" in key "material" instead of the secret
        api_access_value = response["data"].get(next(iter(response["data"])))[
            "material"
        ]
        no_api_access = "noAPIAccess" in api_access_value

        if no_api_access:
            raise PhabfiveDataException(api_access_value.get("noAPIAccess"))

        return response["data"]

    def print_secret(self, ids):
        secret = self.get_secret(ids)

        for value in secret.values():
            for secret_type, secret_value in value["material"].items():
                if secret_type == "password":
                    print(secret_value)
