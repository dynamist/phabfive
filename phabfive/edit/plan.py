# -*- coding: utf-8 -*-
"""Planning task edits, as data, before anything is applied.

An edit of one or more tasks is two steps with a decision between them:
work out what would change on each task, then send it. The command puts a
review, a confirmation or a dry run in between; a program might inspect the
changes, log them or drop some. `plan_task_edits` is the first step and
`Edit.apply` the second, and neither prints, prompts or reads a terminal.

Validation is atomic: every task is fetched and checked for board context
before any edit is built, and if one fails, `PhabfiveValidationException`
names every task that did and nothing is planned at all.
"""

import functools
from dataclasses import dataclass, field

from phabfive.constants import TASK_POLICY_FIELDS
from phabfive.edit.validators import (
    get_board_names,
    get_task_boards,
    validate_board_column_context,
)
from phabfive.exceptions import PhabfiveException, PhabfiveValidationException
from phabfive.maniphest.validators import validate_assignment
from phabfive.policy import resolve_policy_value, validate_policy_value


@dataclass(frozen=True)
class TaskEdit:
    """The changes planned for one task.

    Attributes
    ----------
    task_id : str
        Numeric task ID, e.g. "123".
    transactions : list
        The Conduit transactions that would make the change.
    changes : list
        What they change, as {"field", "old", "new"} dicts. `old` is None
        for an addition, such as a comment.
    """

    task_id: str
    transactions: list
    changes: list

    @property
    def monogram(self):
        return f"T{self.task_id}"

    @property
    def noop(self):
        """Whether the task is already as asked, so nothing would be sent."""
        return not self.transactions


@dataclass(frozen=True)
class EditFailure:
    """A task whose edit could not be planned, and why."""

    task_id: str
    error: Exception

    @property
    def monogram(self):
        return f"T{self.task_id}"


@dataclass(frozen=True)
class ValidationProblem:
    """One task that failed validation.

    Attributes
    ----------
    task_id : str
    message : str
    boards : tuple
        The names of the boards a task is on, when it is on several and no
        board was named to choose between them - enough to suggest how to
        split the edit up.
    """

    task_id: str
    message: str
    boards: tuple = ()

    @property
    def monogram(self):
        return f"T{self.task_id}"


@dataclass
class EditPlan:
    """Everything an edit would do, in the order the tasks were given.

    Attributes
    ----------
    entries : list
        A TaskEdit or an EditFailure per task.
    """

    entries: list
    _confirm: object = field(default=None, repr=False, compare=False)

    @property
    def edits(self):
        """The tasks whose edit was planned, no-ops included."""
        return [entry for entry in self.entries if isinstance(entry, TaskEdit)]

    @property
    def failures(self):
        """The tasks whose edit could not be planned."""
        return [entry for entry in self.entries if isinstance(entry, EditFailure)]

    @functools.cached_property
    def needs_confirmation(self):
        """Whether it would change a title, a description or a policy.

        Those are the changes worth a second look before they are made - a
        retitle or a narrowed view policy looks fine to whoever made it and
        is easy to miss for everybody else. Worked out on first use, because
        comparing a policy means resolving it against the instance.
        """
        return bool(self._confirm and self._confirm())


def _validate(maniphest, task_ids, column, tag, task_data):
    """Fetch and check every task, raising once for all that failed.

    Returns
    -------
    list
        A {"task_id", "task_data", "board_phid"} dict per task.
    """
    problems = []
    validated = []

    for task_id in task_ids:
        try:
            data = task_data.get(task_id) or maniphest._get_task_data(task_id)

            board_phid, error = validate_board_column_context(
                task_id, data, column, tag, maniphest
            )

            if error:
                boards = ()
                if "multiple boards" in error:
                    boards = tuple(
                        get_board_names(get_task_boards(data), maniphest.phab)
                    )
                problems.append(ValidationProblem(task_id, error, boards))
            else:
                validated.append(
                    {"task_id": task_id, "task_data": data, "board_phid": board_phid}
                )

        except Exception as e:
            problems.append(ValidationProblem(task_id, str(e)))

    if problems:
        raise PhabfiveValidationException(problems)

    return validated


def _needs_text_confirmation(validated_tasks, title, description):
    """Whether any task's title or description would actually change."""
    if title is None and description is None:
        return False

    for task in validated_tasks:
        fields = task["task_data"]["fields"]
        if description is not None:
            if fields.get("description", {}).get("raw", "") != description:
                return True
        if title is not None and fields.get("name", "") != title:
            return True

    return False


def _needs_policy_confirmation(validated_tasks, maniphest, visible_to, editable_by):
    """Whether any task's view or edit policy would actually change.

    A policy change joins the text guard rather than applying unreviewed. It
    is the more consequential of the two and the harder to notice: a retitled
    task is still on the board it was on, while one whose view policy has
    narrowed has simply gone - for everybody the new policy leaves out, there
    is nothing left to notice. Both are also silent in the same way, in that
    the object still exists and still looks fine to whoever made the change.

    Deciding it needs the instance, unlike the text guard: `#infra` has to
    become a PHID before it can be compared with the policy in place. That is
    one resolution for the batch, not one per task.
    """
    asked = [
        (TASK_POLICY_FIELDS[key], value, option)
        for key, value, option in (
            ("view", visible_to, "--visible-to"),
            ("edit", editable_by, "--editable-by"),
        )
        if value is not None
    ]

    for field_name, value, option in asked:
        resolved = resolve_policy_value(maniphest.phab, value, option=option)

        for task in validated_tasks:
            policy = task["task_data"]["fields"].get("policy") or {}
            if policy.get(field_name) != resolved:
                return True

    return False


def plan_task_edits(
    maniphest,
    task_ids,
    title=None,
    priority=None,
    status=None,
    tag=None,
    column=None,
    assign=None,
    unassign=False,
    description=None,
    subscribe=None,
    unsubscribe=None,
    attach=None,
    detach=None,
    comment=None,
    space=None,
    visible_to=None,
    editable_by=None,
    task_data=None,
):
    """Plan the same edit on each of `task_ids`, and change nothing.

    Parameters
    ----------
    maniphest : Maniphest
        The app that fetches the tasks and builds their transactions.
    task_ids : list of str
        Numeric task IDs, e.g. ["123", "124"].
    task_data : dict, optional
        Task ID -> task data already fetched, so it is not fetched again.

    The remaining arguments are the changes, as `maniphest edit` takes them:
    `priority` and `column` also take "raise"/"lower" and
    "forward"/"backward", and `tag` names the board a column is on.

    Returns
    -------
    EditPlan

    Raises
    ------
    PhabfiveConfigException
        When a policy value is not one phabfive understands. Checked before
        any task is fetched, because the server would read a typo as a policy
        nobody satisfies and answer with a self-lockout.
    PhabfiveInputException
        When `assign` and `unassign` are both given.
    PhabfiveValidationException
        When any task cannot be fetched or its board context is ambiguous.
        Nothing is planned for any task.
    """
    validate_policy_value(visible_to, option="--visible-to")
    validate_policy_value(editable_by, option="--editable-by")
    validate_assignment(assign, unassign)

    validated = _validate(maniphest, task_ids, column, tag, task_data or {})

    entries = []
    for task in validated:
        task_id = task["task_id"]
        try:
            transactions, changes = maniphest.build_task_edit(
                task_id,
                task["task_data"],
                title=title,
                priority=priority,
                status=status,
                board_phid=task["board_phid"],
                column=column,
                assign=assign,
                unassign=unassign,
                description=description,
                subscribe=subscribe,
                unsubscribe=unsubscribe,
                attach=attach,
                detach=detach,
                comment=comment,
                space=space,
                visible_to=visible_to,
                editable_by=editable_by,
            )
        except PhabfiveException as e:
            entries.append(EditFailure(task_id, e))
        else:
            entries.append(TaskEdit(task_id, transactions, changes))

    def confirm():
        return _needs_text_confirmation(
            validated, title, description
        ) or _needs_policy_confirmation(validated, maniphest, visible_to, editable_by)

    return EditPlan(entries, _confirm=confirm)


__all__ = [
    "EditFailure",
    "EditPlan",
    "TaskEdit",
    "ValidationProblem",
    "plan_task_edits",
]
