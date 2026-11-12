# -*- coding: utf-8 -*-
"""Editing tasks: plan the changes as data, then apply them."""

from phabfive.edit.core import Edit
from phabfive.edit.plan import EditFailure, EditPlan, TaskEdit, ValidationProblem
from phabfive.edit.spec import (
    EditItem,
    EditPlanError,
    EditRecord,
    EditReport,
    EditSpecPlan,
    apply_edit_plan,
    apply_edit_spec,
    plan_edit,
)

__all__ = [
    "Edit",
    "EditFailure",
    "EditItem",
    "EditPlan",
    "EditPlanError",
    "EditRecord",
    "EditReport",
    "EditSpecPlan",
    "TaskEdit",
    "ValidationProblem",
    "apply_edit_plan",
    "apply_edit_spec",
    "plan_edit",
]
