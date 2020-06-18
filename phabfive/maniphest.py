# -*- coding: utf-8 -*-

# python std lib
import logging

# phabfive imports
from phabfive.core import Phabfive


log = logging.getLogger(__name__)


class Maniphest(Phabfive):

    def __init__(self):
        super(Maniphest, self).__init__()

    def add_comment(self, ticket_identifier, comment_string):
        """
        :type ticket_identifier: str
        :type comment_string: str
        """
        result = self.phab.maniphest.edit(
            transactions=[{
                "type": "comment",
                "value": comment_string,
            }],
            objectIdentifier=ticket_identifier,
        )

        return (True, result["object"])

    def info(self, task_id):
        """
        :type task_id: int
        """
        # FIXME: Add validation and extraction of the int part of the task_id
        result = self.phab.maniphest.info(
            task_id=task_id,
        )

        return (True, result)
