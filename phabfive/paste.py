# -*- coding: utf-8 -*-

# python std lib
import re

# phabfive imports
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException


class Paste(Phabfive):
    def __init__(self):
        super(Paste, self).__init__()

    def get_pastes(self, query_key=None, attachments=None, constraints=None):
        """Wrapper that connects to Phabricator and retrieves information about pastes.

        `query_key` defaults to "all".

        :type query_key: str
        :type attachments: dict
        :type constraints: dict

        :rtype: dict
        """
        query_key = "all" if not query_key else query_key
        attachments = {} if not attachments else attachments
        constraints = {} if not constraints else constraints
        response = self.phab.paste.search(
            queryKey=query_key, attachments=attachments, constraints=constraints
        )

        pastes = response.get("data", {})

        if not pastes:
            raise PhabfiveDataException("No data or other error.")

        return pastes

    def print_pastes(self):
        """Method used by the Phabfive CLI.
        """
        pastes = self.get_pastes()

        # sort based on title
        pastes = sorted(pastes, key=lambda key: key["fields"]["title"])

        for paste in pastes:
            name = paste["fields"]["title"]
            print(name)
