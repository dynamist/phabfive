# -*- coding: utf-8 -*-

# python std lib
import logging
import pdb
from pprint import pprint as pp

# phabfive imports
from phabfive.core import Phabfive


log = logging.getLogger(__name__)


class Repl(Phabfive):
    def __init__(self):
        super(Repl, self).__init__()

    def run(self):
        print("*************")
        print("use self.phab to access the phacility API")
        print("use self.conf to access current client configuration")
        print("use pp() to prettyprint the API response back from self.phab.* calls")
        print("*************")

        pdb.set_trace()
