# -*- coding: utf-8 -*-
"""What a create spec creates, and the one call that creates any of it.

`phabfive.spec.create` is the *format*: it turns a spec into a
:class:`~phabfive.spec.create.CreatePlan` and sends the transactions the plan
holds. This subpackage is the *runner*, and the split is the one
`phabfive/search/` already draws for the same reason: building a project
needs `Project` and building a paste needs `Paste`, and `phabfive.spec` may
not import an app class.

    from phabfive import Maniphest
    from phabfive.create import apply_spec, plan_spec
    from phabfive.spec import load_spec

    app = Maniphest(url=URL, token=TOKEN)        # explicit; discovers nothing
    spec = load_spec("sprint.yaml", kind="create").render()

    plan = plan_spec(app, spec)                  # nothing written yet
    for item in plan.items:                      # inspect it, print it, count it
        ...

    for record in apply_plan(app, plan):         # one record per object
        ...

Every app a spec names is built with `Phabfive._from_parent`, so several
object types in one document share one configuration and one client.

Nothing here prints, prompts, exits, reads the environment or reads a
configuration file; the command is `phabfive.cli.maniphest`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from importlib import import_module
from typing import TYPE_CHECKING, Any, Optional

from phabfive.spec.create import (
    CreatePlan,
    CreatePlanError,
    CreateRecord,
    CreateReport,
    apply_plan as _apply_plan,
    apply_spec as _apply_spec,
    plan_create,
)
from phabfive.spec.online import Resolver

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from phabfive.spec.envelope import Spec

__all__ = [
    "CREATE_APPS",
    "app_for",
    "apply_plan",
    "apply_spec",
    "plan_spec",
]


#: The app that creates each object type: its module and its class, imported
#: at call time - the `_LAZY` pattern `phabfive/__init__.py` already uses - so
#: planning a task create never imports `Paste`.
#:
#: Deliberately its own table rather than one shared with
#: `phabfive.search.dispatch.SEARCH_RUNNERS`: that one is keyed on a search
#: method's name and its parameters, which a create has none of. Unifying the
#: two is a later cleanup, not something to do while six issues are in flight.
CREATE_APPS: dict[str, tuple[str, str]] = {
    "task": ("phabfive.maniphest", "Maniphest"),
    "project": ("phabfive.project", "Project"),
    "paste": ("phabfive.paste", "Paste"),
}


def app_for(object_type: str, parent: Any) -> Any:
    """The app that creates this object type, sharing `parent`'s client.

    `parent` is any already-constructed app. The sibling is built with
    `Phabfive._from_parent`, which is the rule `Diffusion.passphrase` and
    `Edit.maniphest` already follow: constructing it instead would read the
    configuration again and connect a second time to the same host. A parent
    that is already the right class is handed back as it is, and so is
    something that is not a `phabfive.core.Phabfive` at all - which is what
    lets a test stand in for an app.

    Raises
    ------
    CreatePlanError
        Nothing creates that object type.
    """
    from phabfive.core import Phabfive

    named = CREATE_APPS.get(object_type)

    if named is None:
        known = ", ".join(sorted(CREATE_APPS))
        raise CreatePlanError(
            f"Nothing creates a {object_type!r}. A create spec holds: {known}",
            check="unsupported-type",
            object_type=object_type,
        )

    module, class_name = named
    app_class = getattr(import_module(module), class_name)

    if isinstance(parent, app_class):
        return parent

    if not isinstance(parent, Phabfive):
        return parent

    return app_class._from_parent(parent)


def plan_spec(
    parent: Any,
    spec: "Spec",
    *,
    validate: bool = True,
    resolvers: Optional[Sequence[Resolver]] = None,
) -> CreatePlan:
    """Everything a create spec would create, whatever object types it holds.

    `phabfive.spec.create.plan_create` with the app worked out from the
    spec rather than handed in. One plan for the whole document; see that
    module for why a create spec gets one plan where a search spec gets one
    per item.

    The `Maniphest` and not a per-item app, and that is not a gap: planning
    needs one app that can answer the questions only Maniphest can - is
    this a priority, is this a status, what columns has this board - and
    `plan_create` asks `app_for` itself for the `Project` a `projects:`
    section needs, at the point it needs it. A sibling shares the client,
    so asking twice costs nothing and connects nothing.
    """
    return plan_create(
        app_for("task", parent), spec, validate=validate, resolvers=resolvers
    )


def apply_plan(
    parent: Any, plan: CreatePlan, *, pace: Any = None
) -> Iterator[CreateRecord]:
    """Create everything the plan describes, one record at a time.

    A generator that never raises because the server refused something; see
    `phabfive.spec.create.apply_plan`, which this is the dispatching form of.

    **One app for every object type, deliberately.** Applying reads exactly
    two things off it - `app.phab`, the Conduit client, and `app.conf`, for
    the `Pacer` - and siblings share both, so which class it is changes
    nothing. What decides where a transaction is sent is the item's own
    object type, through `phabfive.spec.create.EDIT_ENDPOINTS`: a project
    item goes to `project.edit` whatever app is holding the client. Calling
    `app_for` per item here would look like it picked the endpoint and
    would not, which is worse than not calling it.
    """
    return _apply_plan(parent, plan, pace=pace)


def apply_spec(parent: Any, plan: CreatePlan, *, pace: Any = None) -> CreateReport:
    """Every record of one run, for a caller that does not watch it happen."""
    return _apply_spec(parent, plan, pace=pace)
