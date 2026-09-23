# -*- coding: utf-8 -*-
"""Applying a create spec, whatever it creates.

`phabfive.spec` is the *format* and never imports an app class. This
subpackage is the *runner*: it is allowed to know that `Maniphest`,
`Project` and `Paste` exist, which is what one call over a spec holding all
three needs. It still prints nothing, prompts for nothing and exits nothing
- the command lives in `phabfive.cli.maniphest`.

    from phabfive import Maniphest
    from phabfive.create import apply_plan, plan_spec
    from phabfive.spec import load_spec

    app = Maniphest(url=URL, token=TOKEN)        # explicit; discovers nothing
    plan = plan_spec(app, load_spec("sprint.yaml", kind="create").render())

    for record in apply_plan(app, plan):
        print(record.as_record())

The plan is the point: it is frozen, inspectable and serializable, and
nothing has been written when you hold one.
"""

from phabfive.create.dispatch import CREATE_APPS as CREATE_APPS
from phabfive.create.dispatch import app_for as app_for
from phabfive.create.dispatch import apply_plan as apply_plan
from phabfive.create.dispatch import apply_spec as apply_spec
from phabfive.create.dispatch import plan_spec as plan_spec
from phabfive.spec.create import CreateItem as CreateItem
from phabfive.spec.create import CreatePlan as CreatePlan
from phabfive.spec.create import CreatePlanError as CreatePlanError
from phabfive.spec.create import CreateRecord as CreateRecord
from phabfive.spec.create import CreateReport as CreateReport

# Both spellings are load-bearing: __all__ is what mypy's no_implicit_reexport
# reads, `import x as x` is what keeps ruff quiet about an unused import.
__all__ = [
    "CREATE_APPS",
    "CreateItem",
    "CreatePlan",
    "CreatePlanError",
    "CreateRecord",
    "CreateReport",
    "app_for",
    "apply_plan",
    "apply_spec",
    "plan_spec",
]
