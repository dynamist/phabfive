# -*- coding: utf-8 -*-
"""`phabfive hints`: what to know before working here.

What it shows depends on where it runs. Always the instance phabfive would
use and what chose it. Outside a repository, every instance ~/.arcrc holds,
since nothing has picked one. Inside one, its .arcconfig, the workboard with
the description and columns that say how the board is used, and the tag
hints from `phabfive.tags.hints`, each with its project's description and
the repository's note. `maniphest create` reminds of the hinted tags a new
task was not given. Nothing here ever adds a tag: whoever writes the task
decides whether a hint applies.
"""

import logging
import re

import typer

from phabfive.core import RepositoryConfig

log = logging.getLogger(__name__)

HINTS_COMMAND = "phabfive hints"


def repository_hints(app):
    """The tag hints of the repository `app` was configured in.

    None of them when the app was configured for another instance than the
    repository names - a hint names a project on the repository's instance,
    and the same name on another would be a different project, or none.

    Parameters
    ----------
    app : Phabfive
        The app whose discovered configuration to read.

    Returns
    -------
    dict
        Tag to note, or None, in the order the repository wrote them
    """
    repository = getattr(app, "repository", None)
    if not isinstance(repository, RepositoryConfig) or not repository.hints:
        return {}

    host = app.conf.get("PHORGE_HOST")
    if repository.host and isinstance(host, str) and host and repository.host != host:
        log.debug(
            f"Not using the tag hints of {repository.path}: they are for "
            f"{repository.host}, and this command talks to {host}"
        )
        return {}

    return dict(repository.hints)


def _tag_key(value):
    """A tag as a reminder compares it: case, a leading #, and - _ or space folded.

    `security-risk`, `#Security_Risk` and `Security Risk` are the same tag
    here. It needs no request, and is all a reminder needs: a tag given as
    an ID or a PHID is not recognised, which at worst reminds of a tag the
    task already has.
    """
    return re.sub(r"[\s_-]+", "-", value.strip().lstrip("#").strip().lower())


def unmet_hints(hints, tag_lists):
    """The hinted tags that at least one of `tag_lists` lacks.

    Parameters
    ----------
    hints : mapping
        Tag to note, as `repository_hints` gives them.
    tag_lists : list of list of str
        The tags of each task about to be created.

    Returns
    -------
    list
        The hinted tags, as the repository wrote them, in its order
    """
    given = [{_tag_key(tag) for tag in tags} for tags in tag_lists]
    return [hint for hint in hints if any(_tag_key(hint) not in tags for tags in given)]


def remind_of_hints(app, tag_lists):
    """Say on stderr which hinted tags the tasks about to be created lack.

    One line, naming the tags only, so it never crowds the output; the
    notes are what `phabfive hints` is for. Nothing is said when the
    repository has no hints, or every task has them all.

    Parameters
    ----------
    app : Phabfive
        The app creating the tasks.
    tag_lists : list of list of str
        The tags of each task, one list per task.
    """
    if not tag_lists:
        return

    unmet = unmet_hints(repository_hints(app), tag_lists)
    if not unmet:
        return

    where = "" if len(tag_lists) == 1 else " on every task"
    typer.echo(
        f"Not tagged{where}: {', '.join(unmet)} "
        f"(repository hints; see {HINTS_COMMAND})",
        err=True,
    )


def _project_section(app, hint):
    """One project of `Maniphest.tag_hints`, as a section of the record."""
    from phabfive.project.formatters import project_link_url

    section = {}

    if hint.get("id") is not None:
        section["Link"] = project_link_url(app.url, hint)

    section["Tag"] = hint["tag"]
    section["Project"] = hint.get("name")

    if hint.get("problem"):
        section["Problem"] = hint["problem"]

    section["Description"] = hint.get("description")
    return section


def _instance_from(app, repository):
    """Where the instance `app` talks to was chosen, in words."""
    from phabfive.settings import env_setting

    if env_setting("PHORGE_HOST") or env_setting("PHORGE_API_HOST"):
        return "PHORGE_HOST"
    if repository is not None and repository.host == app.conf.get("PHORGE_HOST"):
        return ".arcconfig"
    if app.has_explicit_host():
        return "phabfive's configuration files"
    if len(type(app).arcrc_hosts()) == 1:
        return "~/.arcrc, its only host"
    return "~/.arcrc, its config.default"


def _workboard_from(repository, workboard):
    """Where the workboard was named, in words."""
    from phabfive.settings import env_setting

    if env_setting("PHORGE_WORKBOARD") is not None:
        return "PHORGE_WORKBOARD"
    if repository is not None and repository.workboard == workboard:
        return ".arcconfig"
    return "phabfive's configuration files"


def remind_of_plan_hints(app, plan):
    """`remind_of_hints` for the tasks a create spec's plan would create.

    The projects each task names, as the plan resolved them for its preview;
    anchors create nothing, so they are not reminded of anything.
    """
    remind_of_hints(
        app,
        [
            list(item.display.get("projects") or [])
            for item in plan.items
            if item.object_type == "task" and not item.anchor
        ],
    )


def build_hints_record(app, repository, problem=None):
    """The one record `phabfive hints` shows.

    Each section is there only when it applies, so a program reads one
    shape wherever the command ran: Instance always, Instances outside a
    repository, Repository inside one, Workboard when one is named, Tags
    when the repository hints any.

    Parameters
    ----------
    app : Maniphest or None
        The app, or None when no instance could be chosen.
    repository : RepositoryConfig or None
        The repository's .arcconfig, as `Phabfive.repository_config` reads it.
    problem : str, optional
        Why no app could be constructed.

    Returns
    -------
    dict
        The record, ready for `phabfive.record_display.display_records`
    """
    from phabfive.core import Phabfive

    from phabfive.settings import env_setting

    record = {}

    if app is None:
        # What named an instance, though the configuration around it failed:
        # that host and the error, not a choice nobody was asked to make
        named = env_setting("PHORGE_HOST") or env_setting("PHORGE_API_HOST")
        if named:
            instance = {"URL": named, "From": "PHORGE_HOST"}
        elif repository is not None and repository.host:
            instance = {"URL": repository.host, "From": ".arcconfig"}
        elif len(Phabfive.arcrc_hosts()) > 1:
            instance = {
                "URL": None,
                "From": (
                    "none chosen: ~/.arcrc holds several; pick one with "
                    "PHORGE_HOST, or work in a repository whose .arcconfig "
                    "names one"
                ),
            }
            problem = None
        else:
            instance = {"URL": None, "From": "none configured: run phabfive user setup"}
        if problem:
            instance["Problem"] = problem
        record["Instance"] = instance
    else:
        record["Instance"] = {
            "URL": app.conf.get("PHORGE_HOST"),
            "From": _instance_from(app, repository),
        }

    if repository is None:
        record["Instances"] = Phabfive.arcrc_hosts()
    else:
        record["Repository"] = {
            "Config": repository.path,
            "Instance": repository.host,
        }

    if app is None:
        return record

    if repository is not None and repository.hints and not repository_hints(app):
        record["Repository"]["Problem"] = (
            f"Tag hints left out: they name projects on {repository.host}, "
            f"and phabfive is using {app.conf.get('PHORGE_HOST')}"
        )

    workboard = app.conf.get("PHORGE_WORKBOARD")
    workboard = workboard.strip() if isinstance(workboard, str) else ""
    tag_hints = repository_hints(app)

    if not workboard and not tag_hints:
        return record

    found = app.tag_hints(tag_hints, workboard=workboard or None)

    for hint in found:
        if hint.get("problem"):
            typer.echo(f"Warning: {hint['tag']}: {hint['problem']}", err=True)

        if hint["role"] == "workboard":
            section = _project_section(app, hint)
            section["Columns"] = list(hint.get("columns") or [])
            section["From"] = _workboard_from(repository, workboard)
            record["Workboard"] = section
        else:
            section = _project_section(app, hint)
            section["Note"] = hint.get("note")
            record.setdefault("Tags", []).append(section)

    return record


def hints_command(ctx: typer.Context) -> None:
    """Show what to know before working here: the instance, the board, the tags.

    Always the instance phabfive would use, and what chose it. Outside a
    repository, every instance in ~/.arcrc. Inside one, its .arcconfig: the
    workboard, with the description and columns that say how the board is
    used, and each tag hint with its project's description and the
    repository's note on when it applies. Nothing is changed, and nothing is
    asked of an instance unless there is a workboard or a hint to look up.
    """
    from phabfive.cli.apps import new_app
    from phabfive.cli.output import _get_output_format, _setup_output_options
    from phabfive.core import Phabfive
    from phabfive.exceptions import PhabfiveConfigException
    from phabfive.maniphest import Maniphest
    from phabfive.record_display import display_records

    _setup_output_options(ctx)
    output_format = _get_output_format(ctx)
    repository = Phabfive.repository_config()

    # Neither verified nor prompted for: the command reports the
    # configuration, so several ~/.arcrc hosts and none chosen is an answer
    # to show, not a question to ask
    app, problem = None, None
    try:
        app = new_app(Maniphest, verify=False, select_host=None)
    except PhabfiveConfigException as e:
        problem = str(e)

    record = build_hints_record(app, repository, problem)
    display_records([record], output_format, app or Phabfive.__new__(Phabfive))
