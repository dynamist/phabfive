# -*- coding: utf-8 -*-

# python std lib
import re

# phabfive imports
from phabfive.core import Phabfive
from phabfive.exceptions import PhabfiveDataException
from phabfive.constants import MONOGRAMS


class Paste(Phabfive):
    def __init__(self):
        super(Paste, self).__init__()

    def _validate_identifier(self, id_):
        return re.match("^" + MONOGRAMS["paste"] + "$", id_)

    def _convert_ids(self, ids):
        """Method used by print function."""
        ids_list_int = []

        for id_ in ids:
            if not self._validate_identifier(id_):
                raise PhabfiveDataException(
                    'Identifier "{0}" is not valid.'.format(id_)
                )
            id_ = id_.replace("P", "")
            # constraints takes int
            id_ = int(id_)
            ids_list_int.append(id_)

        return ids_list_int

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

        return pastes

    def print_pastes(self, ids=None):
        """Method used by the Phabfive CLI."""
        if ids:
            constraints = {"ids": self._convert_ids(ids=ids)}
            pastes = self.get_pastes(constraints=constraints)
        else:
            pastes = self.get_pastes()

        if not pastes:
            raise PhabfiveDataException("No data or other error.")

        # sort based on title
        response = sorted(pastes, key=lambda key: key["fields"]["title"])

        for item in response:
            paste = item["fields"]["title"]
            paste_id = "P{0}".format(item["id"])

            print("{0} {1}".format(paste_id, paste))
