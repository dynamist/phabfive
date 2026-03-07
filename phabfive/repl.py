# -*- coding: utf-8 -*-

import code
import logging
import readline
import rlcompleter
from pprint import pprint as pp  # noqa: F401

from phabfive.core import Phabfive

log = logging.getLogger(__name__)

try:
    from ptpython.repl import embed as ptpython_embed

    HAS_PTPYTHON = True
except ImportError:
    HAS_PTPYTHON = False


class Repl(Phabfive):
    def __init__(self):
        super().__init__()

    def run(self):
        print("phabfive REPL")
        print("  phab  - Phabricator API client")
        print("  conf  - Current configuration")
        print("  url   - Server address")
        print("  pp()  - Pretty print API responses")

        namespace = {
            "phab": self.phab,
            "conf": self.conf,
            "url": self.url,
            "pp": pp,
        }

        if HAS_PTPYTHON:
            ptpython_embed(globals=namespace, locals=namespace)
        else:
            readline.set_completer(rlcompleter.Completer(namespace).complete)
            readline.parse_and_bind("tab: complete")
            code.interact(local=namespace, banner="", exitmsg="")
