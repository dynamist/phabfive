# -*- coding: utf-8 -*-
"""The resources block that closes the help of every command group.

An AI agent that meets phabfive for the first time reads --help and nothing
else, so the flag that would teach it the rest has to be advertised there.
"""

from typer.core import TyperGroup

AGENT_HELP_FOOTER = (
    "Config: ~/.config/phabfive.yaml, ~/.arcrc, .arcconfig\n"
    "Env:    PHAB_URL, PHAB_TOKEN (run: phabfive user setup)\n"
    "Docs:   https://phabfive.readthedocs.io\n"
    "Home:   https://github.com/dynamist/phabfive\n"
    "\n"
    "Are you an AI? Use these resources ONLY IF your task specifically asks you to:\n"
    "  Help a human install or configure phabfive:\n"
    "    https://phabfive.readthedocs.io\n"
    "  Read or change Phabricator/Phorge tasks, pastes, repositories or credentials:\n"
    "    SKIP if a phabfive skill is already in your context. Otherwise run: phabfive --skill"
)


class AgentFooterGroup(TyperGroup):
    """Command group whose help ends with the agent resources block.

    The text is written line by line rather than passed to Typer as epilog=,
    because click's default format_epilog runs it through write_text, which
    rewraps the paragraphs and drops the indentation the block is made of.
    """

    def format_epilog(self, ctx, formatter) -> None:
        formatter.write_paragraph()
        for line in AGENT_HELP_FOOTER.splitlines():
            formatter.write(f"{line}\n")
