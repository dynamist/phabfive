# -*- coding: utf-8 -*-

# python std lib
import logging
import os

# phabfive imports
from phabfive.core import Phabfive
from phabfive.constants import *

# 3rd party imports
import yaml
from jinja2 import Template


log = logging.getLogger(__name__)


class Maniphest(Phabfive):
    def __init__(self):
        super(Maniphest, self).__init__()

    def add_comment(self, ticket_identifier, comment_string):
        """
        :type ticket_identifier: str
        :type comment_string: str
        """
        result = self.phab.maniphest.edit(
            transactions=self.to_transactions({"comment": comment_string}),
            objectIdentifier=ticket_identifier,
        )

        return (True, result["object"])

    def info(self, task_id):
        """
        :type task_id: int
        """
        # FIXME: Add validation and extraction of the int part of the task_id
        result = self.phab.maniphest.info(task_id=task_id)

        return (True, result)

    def create_from_config(self, config_file, dry_run=False):
        if not config_file:
            log.error(f"No config file specified")

        if not os.path.exists(config_file):
            log.error(f"Specified config file '{config_file}' do not exists")

        with open(config_file) as stream:
            data = yaml.load(stream, Loader=yaml.Loader)

        #####
        # STEP: Data structure validation, check title + description, projects existance

        ### Validate all projects is correct data types and exists in Phabricator instance
        projects_query = self.phab.project.search(constraints={"name": ""})

        project_name_to_id_mapping = {
            project["fields"]["name"]: project["phid"]
            for project in projects_query["data"]
        }

        log.debug("All Phabricator project names")
        log.debug(project_name_to_id_mapping)

        for index, ticket_data in enumerate(data["tickets"]):
            if "projects" not in ticket_data:
                break

            if not isinstance(ticket_data["projects"], list):
                log.error(f"data key 'projects' must be of list type")
                return (False, None)

            has_errors = False

            for project in ticket_data["projects"]:
                if project not in project_name_to_id_mapping:
                    log.error(f"Project name '{project}' not found in Phabricator instance")
                    has_errors = True

            if has_errors:
                log.error(f"Could not find one or more specified projects in phabricator instance. All available project names")
                log.error(list(project_name_to_id_mapping.keys()))
                return (False, None)

        #####
        # STEP: Render
        log.debug(data)

        # Define the variables to be used in the template
        variables = data["variables"]

        rendered_data = []

        for ticket_data in data["tickets"]:
            rendered_data.append({
                "title": Template(ticket_data["title"]).render(variables),
                "description": Template(ticket_data["description"]).render(variables),
                "projects": ticket_data["projects"],
            })

        log.debug(rendered_data)

        #####
        # STEP: Post render data validation
        for ticket_data in rendered_data:
            if not isinstance(ticket_data["title"], str):
                log.error("Ticket title must be a string")

            if not isinstance(ticket_data["description"], str):
                log.error("Ticket description must be a string")

        #####
        # STEP: Run
        if dry_run:
            log.critical("Running with --dry-run, tickets will NOT be commited to phabricator")

        result_ids = []

        for ticket_data in rendered_data:
            # Prepare the data to submit for each ticket
            transactions = [
                {"type": "title", "value": ticket_data["title"]},
                {"type": "description", "value": ticket_data["description"]},
                {"type": "priority", "value": ticket_data.get("priority", TICKET_PRIORITY_NORMAL)},
            ]

            if ticket_data.get("projects"):
                transactions.append({
                    "type": "projects.set",
                    "value": [
                        project_name_to_id_mapping[project_name]
                        for project_name in ticket_data["projects"]
                    ],
                })

            log.debug("transactions for ticket")
            log.debug(transactions)

            if not dry_run:
                result = self.phab.maniphest.edit(
                    transactions=transactions,
                )

                result_ids.append(result["object"]["id"])

                log.debug("ticket create result")
                log.debug(result)

            log.info("Created ticket")

        #####
        # STEP: Report
        if dry_run:
            log.critical("Running with --dry-run, no ticket details will be reported as nothing was created")
            return (True, None)

        log.info("Report of all created tickets")
        for result_id in result_ids:
            _, ticket_info = self.info(int(result_id))
            log.info(f"Title: {ticket_info['title']} | ID: {ticket_info['id']} | URI: {ticket_info['uri']}")
