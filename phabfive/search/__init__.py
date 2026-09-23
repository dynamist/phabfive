# -*- coding: utf-8 -*-
"""Running a search spec, whatever it searches.

`phabfive.spec` is the *format* and never imports an app. This subpackage is
the *runner*: it is allowed to know that `Maniphest`, `Project`, `Paste` and
`Passphrase` exist, which is what one call over a spec holding all four
needs. It still prints nothing, prompts for nothing and exits nothing - the
command lives in `phabfive.cli.search_spec`.

    from phabfive import Project
    from phabfive.spec import load_spec
    from phabfive.search import records_of, run_spec

    project = Project(url=URL, token=TOKEN)      # explicit; discovers nothing
    for result in run_spec(project, load_spec("mine.yaml", kind="search")):
        for record in records_of(result):
            ...

Every app a spec names is built with `Phabfive._from_parent`, so four object
types in one document share one configuration and one client.
"""

from phabfive.search.dispatch import SEARCH_RUNNERS as SEARCH_RUNNERS
from phabfive.search.dispatch import SearchRunner as SearchRunner
from phabfive.search.dispatch import app_for as app_for
from phabfive.search.dispatch import has_criteria as has_criteria
from phabfive.search.dispatch import plan_item as plan_item
from phabfive.search.dispatch import records_of as records_of
from phabfive.search.dispatch import run_item as run_item
from phabfive.search.dispatch import run_spec as run_spec
from phabfive.search.dispatch import runner_for as runner_for
from phabfive.search.dispatch import searched_text as searched_text

# Both spellings are load-bearing: __all__ is what mypy's no_implicit_reexport
# reads, `import x as x` is what keeps ruff quiet about an unused import.
__all__ = [
    "SEARCH_RUNNERS",
    "SearchRunner",
    "app_for",
    "has_criteria",
    "plan_item",
    "records_of",
    "run_item",
    "run_spec",
    "runner_for",
    "searched_text",
]
