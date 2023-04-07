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

        #projects_query = self.phab.project.search(constraints={"name": ""})
        #Upgrade to handle phab instances with more than 100 project/tags

        #create a new construct to consolidate all project/tags
        projects_query = {}
        projects_query["data"] = []

        r = self.phab.project.search(constraints={"name": ""})
        length = len(r.data)
        a = r['cursor']['after']
        projects_query["data"].extend(r.data)

        while (length >= 100 and a is not None):
            r = self.phab.project.search(constraints={"name": ""},after=a)
            length = len(r.data)
            a = r['cursor']['after']
            projects_query["data"].extend(r.data)

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
                log.error(f"Could not find one or more specified users, projects in phabricator instance. All available project names")
                log.error(list(project_name_to_id_mapping.keys()))
                log.error(f"All available users names")
                log.error(list(user_name_to_id_mapping.keys()))
                return (False, None)

        #create a new construct to consolidate all users for owner and subscriber
        users_query = {}
        users_query["data"] = []

        r = self.phab.user.search()
        length = len(r.data)
        afterCursor = r['cursor']['after']
        users_query["data"].extend(r.data)

        while (length >= 100 and afterCursor is not None):
            r = self.phab.user.search(
                after=afterCursor)
            length = len(r.data)
            afterCursor = r['cursor']['after']
            users_query["data"].extend(r.data)

        user_name_to_id_mapping = {
            user["fields"]["username"]: user["phid"]
            for user in users_query["data"]
        }

        log.debug("All Phabricator user names")
        log.debug(user_name_to_id_mapping)


        '''NEED TO HANDLE Subscriber, Owner'''
        for index, ticket_data in enumerate(data["tickets"]):
            if ("owner" not in ticket_data):
                break

        ####?????
        #    if not isinstance(ticket_data["owner"], list):
        #        log.error(f"data key 'owner' must be of list type")
        #        return (False, None)
        #        print(f"data key 'owner' must be of list type")

            has_errors = False

            if ticket_data["owner"] not in user_name_to_id_mapping:
                log.error(f"Owner User name '{ticket_data['owner']}' not found in Phabricator instance")
                has_errors = True

            if has_errors:
                log.error(f"Could not find one or more specified users, projects in phabricator instance. All available project names")
                log.error(list(user_name_to_id_mapping.keys()))
                log.error(f"All available users names")
                log.error(list(user_name_to_id_mapping.keys()))
                return (False, None)

        for index, ticket_data in enumerate(data["tickets"]):
            if ("subscribers" not in ticket_data):
                break

            if not isinstance(ticket_data["subscribers"], list):
                log.error(f"data key '{subscriber}' must be of list type")
                return (False, None)

            has_errors = False
            
            for subscriber in ticket_data["subscribers"]:

                if subscriber not in user_name_to_id_mapping:
                    log.error(f"Subscriber user name '{subscriber}' not found in Phabricator instance")
                    has_errors = True

            if has_errors:
                log.error(f"Could not find one or more specified users, projects in phabricator instance. All available project names")
                log.error(list(project_name_to_id_mapping.keys()))
                log.error(f"All available users names")
                log.error(list(user_name_to_id_mapping.keys()))
                return (False, None)

        ##### PRIORITY
        for index, ticket_data in enumerate(data["tickets"]):
            if ("priority" not in ticket_data):
                break
            if ticket_data["priority"] not in TICKET_PRIORITIES:
                log.error(f"Priority '{ticket_data['priority']}' not found in Phabricator instance")
                has_errors = True

            if has_errors:
                log.error(f"Could not find one or more specified users, projects in phabricator instance. All available project names")
                log.error(list(project_name_to_id_mapping.keys()))
                log.error(f"All available users names")
                log.error(list(user_name_to_id_mapping.keys()))
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

            })

            r = len(rendered_data)
            if ("owner" in ticket_data):
                rendered_data[r-1]["owner"] = ticket_data["owner"]
            if ("priority" in ticket_data):
                rendered_data[r-1]["priority"] = ticket_data["priority"]
            if ("projects" in ticket_data):
                rendered_data[r-1]["projects"] = ticket_data["projects"]
            if ("subscribers" in ticket_data):
                rendered_data[r-1]["subscribers"] = ticket_data["subscribers"]

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
            ]

            if ticket_data.get("priority"):
                transactions.append({
                    "type": "priority", "value": ticket_data["priority"]},)
            else:
                transactions.append({
                    "type": "priority", "value": ticket_data.get("priority", TICKET_PRIORITY_NORMAL)},)

            if ticket_data.get("projects"):
                transactions.append({
                    "type": "projects.set",
                    "value": [
                        project_name_to_id_mapping[project_name]
                        for project_name in ticket_data["projects"]
                    ],
                })

            if ticket_data.get("subscriber"):
                transactions.append({
                    "type": "subscribers.add",
                    "value": [
                        user_name_to_id_mapping[subscriber]
                        for subscriber in ticket_data["subscribers"]
                    ],
                })

            if ticket_data.get("owner"):
                transactions.append({
                    "type": "owner",
                    "value": 
                        user_name_to_id_mapping[ticket_data["owner"]],
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
