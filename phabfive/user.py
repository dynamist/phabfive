# -*- coding: utf-8 -*-

# python std lib
import logging

# 3rd party imports
from phabricator import APIError

# phabfive imports
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveRemoteException

log = logging.getLogger(__name__)


class User(Phabfive):
    def __init__(self):
        super(User, self).__init__()

    def whoami(self):
        """Return filtered user info dict with userName, realName, primaryEmail, uri."""
        try:
            response = self.phab.user.whoami()
        except APIError as e:
            raise PhabfiveRemoteException(e)

        return {
            key: value
            for (key, value) in response.items()
            if key in ["userName", "realName", "primaryEmail", "uri"]
        }
