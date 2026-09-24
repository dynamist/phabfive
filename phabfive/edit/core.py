# -*- coding: utf-8 -*-
"""Editing tasks: plan the changes as data, then apply them.

The terminal around an edit - where the IDs come from, review, confirmation,
previews and what is printed - is phabfive.cli.edit_flow.
"""

import functools
import logging

from phabfive.core import Phabfive
from phabfive.edit.plan import plan_task_edits
from phabfive.exceptions import PhabfiveInputException
from phabfive.maniphest import Maniphest
from phabfive.retry import Pacer

log = logging.getLogger(__name__)


class Edit(Phabfive):
    """Plan edits of tasks as data, and apply them."""

    @functools.cached_property
    def maniphest(self):
        """A Maniphest sharing this instance's configuration and client."""
        return Maniphest._from_parent(self)

    def plan(
        self,
        object_ids,
        *,
        title=None,
        priority=None,
        status=None,
        tag=None,
        untag=None,
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
    ):
        """Work out what an edit would change on each task, and change nothing.

        Parameters
        ----------
        object_ids : str or iterable
            Tasks, as "T1,T2", or an iterable of monograms ("T1") and
            numeric IDs (1 or "1").

        The changes are keyword arguments, as `maniphest edit` takes them:
        `priority` also takes "raise"/"lower", `column` also takes
        "forward"/"backward". `tag` and `untag` add and remove projects,
        by name, hashtag, ID or PHID, and the first `tag` is also the board
        `column` is on.

        Returns
        -------
        EditPlan
            A TaskEdit or an EditFailure per task, in the order given.

        Raises
        ------
        PhabfiveInputException
            When an ID is not a task's, or a project is both tagged and
            untagged.
        PhabfiveNotFoundException
            When a project to tag or untag does not exist.
        PhabfiveValidationException
            When any task cannot be fetched, or its board is ambiguous. No
            edit is planned for any of them.
        """
        return plan_task_edits(
            self.maniphest,
            self._task_ids(object_ids),
            title=title,
            priority=priority,
            status=status,
            tag=tag,
            untag=untag,
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

    def apply(self, task_edit):
        """Make one planned edit.

        A no-op sends nothing. Returns {"task_id", "changes"}.

        Raises
        ------
        PhabfiveDataException
            When the server refuses the edit.
        """
        if not task_edit.noop:
            self.maniphest.apply_task_edit(task_edit.task_id, task_edit.transactions)
        return {"task_id": task_edit.task_id, "changes": task_edit.changes}

    def apply_all(self, plan):
        """Make every planned edit, in order, stopping at the first refused.

        A task in `plan.failures` was never planned and is not attempted.
        To carry on past a refusal, call `apply` for each edit instead.
        PHAB_PACE, when set, is kept between one write and the next.
        """
        pacer = Pacer.from_conf(self.conf)
        results = []
        for task_edit in plan.edits:
            if not task_edit.noop:
                pacer.wait()
            results.append(self.apply(task_edit))
        return results

    def _task_ids(self, object_ids):
        """Numeric task IDs from "T1,T2", or from monograms and numbers."""
        if isinstance(object_ids, str):
            parsed = self.parse_object_ids(object_ids)
        else:
            parsed = [
                ("task", str(oid))
                if isinstance(oid, int) or str(oid).isdigit()
                else self.parse_monogram(str(oid))
                for oid in object_ids
            ]

        for object_type, oid in parsed:
            if object_type != "task":
                raise PhabfiveInputException(
                    f"Only tasks can be edited, not a {object_type}: {oid}"
                )
        return [oid for _type, oid in parsed]


__all__ = ["Edit"]
