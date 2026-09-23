# -*- coding: utf-8 -*-

"""Jinja2 variables for every spec kind.

This is the engine that used to live in ``phabfive/maniphest/utils.py`` and was
reachable only through task creation. Nothing in it is about Maniphest: a
search spec, a project spec and a paste spec all parameterise the same way.

Three things a caller does with it:

``resolve_variables(declared, overrides)``
    Turn a spec's ``variables:`` section plus whatever the caller supplied
    (``spec.render(variables=...)``, which ``--set name=value`` spells on the
    command line) into one flat mapping of rendered values.

``render_tree(body, variables)``
    Render every string in a nested body against those values.

``render_string(text, variables)``
    Render one string.

An **undefined variable is an error**, not the empty string. Jinja2's default
``Undefined`` renders as ``""``, so ``"Sprint {{ sprint_numbr }} planning"``
quietly became ``"Sprint  planning"`` and the task was created with the wrong
title. Every entry point here uses :class:`jinja2.StrictUndefined` and raises
:class:`~phabfive.exceptions.PhabfiveDataException` naming the variable. There
is no leniency flag; the opt-out is Jinja2's own ``{{ x | default("y") }}``,
which keeps working because the ``default`` filter tests for ``Undefined``
before touching the value.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from jinja2 import Environment, StrictUndefined, meta
from jinja2.exceptions import TemplateError, UndefinedError

from phabfive.exceptions import PhabfiveDataException

log = logging.getLogger(__name__)

__all__ = [
    "DECLARATION_KEYS",
    "build_dependency_graph",
    "declared_value",
    "detect_circular_dependencies",
    "extract_variable_dependencies",
    "is_declaration",
    "missing_variables",
    "render_string",
    "render_tree",
    "render_variables_with_dependency_resolution",
    "resolve_variables",
    "topological_sort",
    "undefined_names",
]

# A `variables:` entry whose value is a mapping with exactly these keys
# declares a variable rather than being one. Exactly one key in Phase 1, so
# that the rule stays easy to state: see `is_declaration`.
DECLARATION_KEYS = frozenset({"default"})

# Nothing in a spec is a template of its own, so one environment is shared.
# StrictUndefined is the whole point of it; `keep_trailing_newline` is off,
# matching what `jinja2.Template(...)` did before.
_ENVIRONMENT = Environment(undefined=StrictUndefined)

# A rendered-in message quotes the template it failed on; a 2000-line
# description in an exception helps nobody.
_SOURCE_EXCERPT = 120


def _excerpt(source: str) -> str:
    """The template string, short enough to read inside an error message."""
    collapsed = " ".join(source.split())

    if len(collapsed) <= _SOURCE_EXCERPT:
        return collapsed

    return collapsed[: _SOURCE_EXCERPT - 1] + "…"


def extract_variable_dependencies(template_str: str) -> set[str]:
    """
    Extract variable names referenced in a Jinja2 template string.
    """
    try:
        ast = _ENVIRONMENT.parse(template_str)
        return meta.find_undeclared_variables(ast)
    except Exception as e:
        log.warning(f"Failed to parse template '{template_str}': {e}")
        return set()


def undefined_names(template_str: str, variables: Mapping[str, object]) -> list[str]:
    """Which names `template_str` reads that `variables` does not supply.

    Offline and cheap: it parses, it does not render, which is what
    `validate_offline` wants when it reports an `undefined-variable` problem
    without committing to a value for everything else in the document.
    """
    return sorted(
        name
        for name in extract_variable_dependencies(template_str)
        if name not in variables
    )


def build_dependency_graph(variables: Mapping[str, object]) -> dict[str, set[str]]:
    """
    Build a dependency graph mapping each variable to its dependencies.
    """
    graph: dict[str, set[str]] = {}

    for var_name, var_value in variables.items():
        if isinstance(var_value, str):
            # Extract dependencies and filter to only include string variables
            # (non-strings don't need rendering, so no ordering constraint)
            dependencies = extract_variable_dependencies(var_value)
            graph[var_name] = {
                dep
                for dep in dependencies
                if dep in variables and isinstance(variables[dep], str)
            }
        else:
            # Non-string values have no dependencies
            graph[var_name] = set()

    return graph


def detect_circular_dependencies(graph: dict[str, set[str]]) -> tuple[bool, list[str]]:
    """
    Detect circular dependencies using depth-first search.
    Returns (has_cycle, cycle_path) tuple.
    """
    visited: set[str] = set()
    recursion_stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> Optional[list[str]]:
        visited.add(node)
        recursion_stack.add(node)
        path.append(node)

        for dependency in graph.get(node, set()):
            if dependency not in visited:
                cycle = dfs(dependency)
                if cycle is not None:
                    return cycle
            elif dependency in recursion_stack:
                # Found a cycle - build the cycle path
                cycle_start_idx = path.index(dependency)
                cycle_path = path[cycle_start_idx:] + [dependency]
                return cycle_path

        _ = path.pop()
        recursion_stack.remove(node)
        return None

    for node in graph:
        if node not in visited:
            result = dfs(node)
            if result is not None:
                return (True, result)

    return (False, [])


def topological_sort(graph: dict[str, set[str]]) -> list[str]:
    """
    Perform topological sort using DFS.
    Returns variables in dependency order (dependencies before dependents).
    """
    visited: set[str] = set()
    result: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)

        # Visit all dependencies first
        for dependency in graph.get(node, set()):
            if dependency not in visited:
                dfs(dependency)

        # Add current node after all dependencies
        result.append(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return result


def render_string(
    template_str: str,
    variables: Mapping[str, object],
    *,
    where: Optional[str] = None,
) -> str:
    """Render one string against `variables`, refusing an undefined name.

    Parameters
    ----------
    template_str : str
        The Jinja2 source.
    variables : Mapping[str, object]
        The values to render with.
    where : str, optional
        What is being rendered, for the error message: "variable 'greeting'",
        "tasks[0].title". Omitted when the caller has nothing better to say
        than the template itself.

    Raises
    ------
    PhabfiveDataException
        If the template names a variable nothing supplies, or fails to
        compile. The message names the variable and the `| default(...)`
        opt-out.
    """
    try:
        return _ENVIRONMENT.from_string(template_str).render(variables)
    except UndefinedError as exception:
        raise PhabfiveDataException(
            _undefined_message(template_str, variables, exception, where)
        ) from exception
    except TemplateError as exception:
        location = f" in {where}" if where else ""
        raise PhabfiveDataException(
            f"Failed to render '{_excerpt(template_str)}'{location}: {exception}"
        ) from exception


def _undefined_message(
    template_str: str,
    variables: Mapping[str, object],
    exception: UndefinedError,
    where: Optional[str],
) -> str:
    """One sentence naming the undefined variable and how to answer for it."""
    missing = undefined_names(template_str, variables)
    location = f" in {where}" if where else f" in '{_excerpt(template_str)}'"

    if not missing:
        # `{{ user.name }}` where `user` is defined but has no `name`: the
        # undefined thing is an attribute, and Jinja2's own message is the
        # one that names it.
        return f"Undefined value{location}: {exception}"

    names = ", ".join(repr(name) for name in missing)
    first = missing[0]

    return (
        f"Undefined variable {names}{location}: declare it under variables:, "
        f"supply it with --set, or write "
        f'{{{{ {first} | default("...") }}}} to allow it to be missing'
    )


def render_tree(value: Any, variables: Mapping[str, object]) -> Any:
    """Render every string in a nested structure, returning a new one.

    Mappings and sequences are walked; mapping *keys* are spec keys rather
    than user text and are left alone. Anything that is not a string, mapping
    or sequence comes back as it went in, so an int stays an int.

    `variables` is what `resolve_variables` returned: already rendered, so a
    body is rendered in one pass.
    """
    return _render_tree(value, variables, path="")


def _render_tree(value: Any, variables: Mapping[str, object], *, path: str) -> Any:
    if isinstance(value, str):
        return render_string(value, variables, where=path or None)

    if isinstance(value, Mapping):
        return {
            key: _render_tree(
                item,
                variables,
                path=f"{path}.{key}" if path else str(key),
            )
            for key, item in value.items()
        }

    # A str is a Sequence and was answered above; bytes and bytearray are not
    # text to render
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [
            _render_tree(item, variables, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]

    return value


def is_declaration(value: object) -> bool:
    """Whether a `variables:` entry declares a variable instead of being one.

    The rule, stated once because the ambiguity is real: an entry is a
    **declaration** only when it is a mapping whose keys are exactly
    `DECLARATION_KEYS` — today, `{default: ...}` and nothing else. Every other
    value is the variable's literal value, so a variable that genuinely *is* a
    mapping keeps working:

        variables:
          sprint: {default: 42}        # declaration, default 42
          workgroup: {lead: alice}     # value, a mapping
          empty: {}                    # value, an empty mapping

    A mapping that means to be a declaration but misspells the key
    (`{defualt: 42}`) is therefore a value. That is the price of letting a
    mapping be a value at all, and it is why the vocabulary is one word.
    """
    return isinstance(value, Mapping) and set(value.keys()) == set(DECLARATION_KEYS)


def declared_value(declaration: object) -> tuple[bool, object]:
    """What a `variables:` entry supplies, as `(supplied, value)`.

    `sprint: {default: 42}` supplies 42, and so does `sprint: 42`. A bare
    `sprint:` — which YAML reads as None — declares the variable without
    supplying anything, so `supplied` is False and it has to be passed in.

    A pair rather than a sentinel, because None is a value a caller may
    legitimately supply and `{default: null}` legitimately declares it.
    """
    if isinstance(declaration, Mapping) and set(declaration.keys()) == set(
        DECLARATION_KEYS
    ):
        return (True, declaration["default"])

    if declaration is None:
        return (False, None)

    return (True, declaration)


def missing_variables(
    declared: Optional[Mapping[str, object]] = None,
    overrides: Optional[Mapping[str, object]] = None,
) -> list[str]:
    """Declared variables that have neither a value, a default, nor an override.

    What `resolve_variables` refuses, offered separately so that
    `validate_offline` can report every one of them as a problem instead of
    stopping at the first.
    """
    declared = declared or {}
    overrides = overrides or {}

    return sorted(
        name
        for name, declaration in declared.items()
        if name not in overrides and not declared_value(declaration)[0]
    )


def resolve_variables(
    declared: Optional[Mapping[str, object]] = None,
    overrides: Optional[Mapping[str, object]] = None,
) -> dict[str, object]:
    """The effective, rendered variables for a spec.

    Parameters
    ----------
    declared : Mapping[str, object], optional
        The spec's `variables:` section, exactly as written. A value may be a
        plain value or a `{default: ...}` declaration; see `is_declaration`.
        A bare `name:` declares a variable with no default, which must be
        supplied.
    overrides : Mapping[str, object], optional
        What the caller supplied — `Spec.render(variables={"sprint": 43})`,
        which `--set sprint=43` spells on the command line. An override beats
        a declared default, and an override for a name the spec never declared
        is kept, so a spec may use `{{ sprint }}` without declaring it as long
        as every caller passes one.

    Returns
    -------
    dict[str, object]
        Name to value, with every string value rendered, so variables may
        refer to one another in any order.

    Raises
    ------
    PhabfiveDataException
        If a declared variable has neither a value nor a default and none was
        supplied, if the variables refer to one another in a cycle, or if one
        of them names something undefined.
    """
    declared = declared or {}
    overrides = overrides or {}

    effective: dict[str, object] = {}
    missing: list[str] = []

    for name, declaration in declared.items():
        if name in overrides:
            effective[name] = overrides[name]
            continue

        supplied, value = declared_value(declaration)

        if not supplied:
            missing.append(name)
            continue

        effective[name] = value

    # An override for a name the spec never declared is still usable: a spec
    # may read `{{ sprint }}` and leave supplying it to whoever runs it
    for name, value in overrides.items():
        if name not in effective:
            effective[name] = value

    if missing:
        names = ", ".join(repr(name) for name in sorted(missing))
        raise PhabfiveDataException(
            f"Variable {names} has no value: give it a default under "
            f"variables: or supply it with --set"
        )

    return render_variables_with_dependency_resolution(effective)


def render_variables_with_dependency_resolution(
    variables: Mapping[str, object],
) -> dict[str, object]:
    """
    Render Jinja2 template variables with proper dependency resolution.

    Analyzes variable dependencies, detects circular references, performs topological
    sorting, and renders variables in the correct order so all dependencies are
    resolved before being referenced.

    Raises
    ------
    PhabfiveDataException
        If circular dependencies are detected in the variable definitions, or
        if a variable names something nothing defines.
    """
    log.debug("Building dependency graph for variables")
    graph = build_dependency_graph(variables)
    log.debug(
        f"Dependency graph: {json.dumps({k: list(v) for k, v in graph.items()}, indent=2)}"
    )

    # Detect circular dependencies
    has_cycle, cycle_path = detect_circular_dependencies(graph)
    if has_cycle:
        cycle_str = " → ".join(cycle_path)
        raise PhabfiveDataException(
            f"Circular reference detected in variables: {cycle_str}"
        )

    # Sort variables in dependency order
    sorted_vars = topological_sort(graph)
    log.debug(f"Topologically sorted variables: {sorted_vars}")

    # Render variables in order
    rendered = dict(variables)
    for var_name in sorted_vars:
        var_value = rendered[var_name]
        if isinstance(var_value, str):
            rendered[var_name] = render_string(
                var_value, rendered, where=f"variable {var_name!r}"
            )
            log.debug(f"Rendered variable '{var_name}': {rendered[var_name]}")

    return rendered
