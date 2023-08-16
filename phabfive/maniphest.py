# -*- coding: utf-8 -*-

# python std lib
import json
import logging
import os
import sys

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
            root_data = yaml.load(stream, Loader=yaml.Loader)

        # Prefetch all users in phabricator, used by subscribers mapping later
        users_query = self.phab.user.search()
        username_to_id_mapping = {
            user["fields"]["username"]: user["phid"]
            for user in users_query["data"]
        }
        log.debug(username_to_id_mapping)

        # Pre-fetch all projects in phabricator, used to map ticket -> projects later
        projects_query = self.phab.project.search(constraints={"name": ""})
        project_name_to_id_mapping = {
            project["fields"]["name"]: project["phid"]
            for project in projects_query["data"]
        }

        log.debug(project_name_to_id_mapping)

        variables = root_data["variables"]
        del root_data["variables"]

        def r(data_block, variable_name, variables):
            data = data_block.get(variable_name, None)
            if data:
                data_block[variable_name] = Template(data).render(variables)

        def pre_process_tasks(task_config):
            """
            This is the main parser that can be run recurse in order to sort out an individual ticket and recurse down
            to pre process each task and to query all internal ID:s and update the datastructure
            """
            log.debug("Pre processing config")
            log.debug(task_config)

            output = task_config.copy()

            # Render strings that should be possible to render with Jinja
            r(output, "title", variables)
            r(output, "description", variables)

            has_errors = False

            # Validate and translate project names to internal project PHID:s
            processed_projects = []
            for project_name in output.get("projects", []):
                mapped_name_id = project_name_to_id_mapping.get(project_name, None)
                if mapped_name_id:
                    processed_projects.append(mapped_name_id)
                else:
                    log.critical(f"Specified project '{project_name}' is not found on the phabricator server")
                    has_errors = True

            output["projects"] = processed_projects

            # Validate and translate all subscriber users to PHID:s
            processed_users = []
            for subscriber_name in output.get("subscribers", []):
                log.debug(f"processing user {subscriber_name}")
                mapped_username_id = username_to_id_mapping.get(subscriber_name, None)
                if mapped_username_id:
                    processed_users.append(mapped_username_id)
                else:
                    log.critical(f"Specified subscriber '{subscriber_name}' not found as a user on the phabricator server")
                    has_errors = True

            output["subscribers"] = processed_users

            if has_errors:
                log.critical(f"Hard errors found during pre-processing step. Please update config or phabricator server and try again. Nothing has been commited yet.")
                sys.exit(1)

            # Recurse down and process all child tasks
            processed_tasks = []

            child_tasks = task_config.get("tasks", None)
            if child_tasks:
                for task in child_tasks:
                    processed_tasks.append(
                        pre_process_tasks(task)
                    )

                output["tasks"] = processed_tasks
            else:
                output["tasks"] = None

            return output

        def recurse_build_transactions(task_config):
            """
            This block recurses over all tasks and builds the transaction set for this ticket and stores it
            in the data structure.
            """
            log.debug("Building transactions for task_config")
            log.debug(task_config)

            # In order to not cause issues with injecting data in a recurse traversal, copy the input,
            # modify the data and return data that is later used to build a new full data structure
            output = task_config.copy()

            # Helper lambda to slim down transaction handling
            add_transaction = lambda t, transaction_type, value : t.append({"type": transaction_type, "value": value})

            transactions = []

            if "title" in task_config and "description" in task_config:
                add_transaction(transactions, "title", task_config["title"])
                add_transaction(transactions, "description", task_config["description"])
                add_transaction(transactions, "priority", task_config.get("priority", TICKET_PRIORITY_NORMAL))

                projects = task_config.get("projects", [])

                if projects:
                    add_transaction(transactions, "projects.set", projects)

                subscribers = task_config.get("subscribers", [])

                if subscribers:
                    add_transaction(transactions, "subscribers.set", subscribers)

                # Prepare all parent and subtasks, and check if we have a parent task from the config file
                subtasks = task_config.get("subtasks", [])

                if subtasks:
                    subtasks_phids = []

                    for ticket_id in subtasks:
                        search_result = self.phab.maniphest.search(
                            constraints={"ids": [int(ticket_id)]},
                        )

                        if len(search_result["data"]) != 1:
                            log.critical(f"Unable to find subtasks ticket with ID={ticket_id}")
                            sys.exit(1)

                        subtasks_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "subtasks.set", subtasks_phids)

                parents = task_config.get("parents", [])

                if parents:
                    parent_phids = []

                    for ticket_id in parents:
                        search_result = self.phab.maniphest.search(
                            constraints={"ids": [int(ticket_id)]},
                        )

                        if len(search_result["data"]) != 1:
                            log.critical(f"Unable to find parent ticket with ID={ticket_id}")
                            sys.exit(1)

                        parent_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "parents.set", parent_phids)
            else:
                log.warning("Required fields 'title' and 'description' is not present in this data block, skipping ticket creation")

            output["transactions"] = transactions

            processed_child_tasks = []
            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                # If there is child tasks to create, recurse down to all of them one by one
                for task in child_tasks:
                    processed_child_tasks.append(
                        recurse_build_transactions(task)
                    )
            else:
                processed_child_tasks = None

            output["tasks"] = processed_child_tasks

            return output

        def recurse_commit_transactions(task_config, parent_task_config):
            """
            This recurse functions purpose is to iterate over all tickets, commit them to phabricator
            and link them to eachother via the ticket hiearchy or explicit parent/subtask links.

            task_config is the current task to create and the parent_task_config is if we have a tree
            of tickets defined in our config file.
            """
            log.debug("\n -- Commiting task")
            log.debug(json.dumps(task_config,indent=2))
            log.debug(" ** parent block")
            log.debug(json.dumps(parent_task_config, indent=2))

            transactions_to_commit = task_config.get("transactions", [])

            if dry_run:
                log.critical("Running with --dry-run, tickets !!WILL NOT BE!! commited to phabricator")
            else:
                if transactions_to_commit:
                    # Parent ticket based on the task hiearchy defined in the config file we parsed is different
                    # from the explicit "ticket parent" that can be defined 
                    if parent_task_config and "phid" in parent_task_config:
                        transactions_to_commit.append({
                            "type": "parents.add",
                            "value": [parent_task_config["phid"]],
                        })

                    log.debug(" -- transactions to commit")
                    log.debug(transactions_to_commit)

                    if dry_run:
                        log.critical("Running with --dry-run, tickets !!WILL NOT BE!! commited to phabricator")
                    else:
                        result = self.phab.maniphest.edit(
                            transactions=transactions_to_commit,
                        )

                    # Store the newly created ticket ID in the data structure so child tickets can look it up
                    task_config["phid"] = str(result["object"]["phid"])
                else:
                    # log.warning(f"No transactions found for ")
                    log.warning("No transactions to commit here, either a bug or root object that can't be transacted")

            child_tasks = task_config.get("tasks", None)
            if child_tasks:
                for child_task in child_tasks:
                    recurse_commit_transactions(child_task, task_config)

        # Main task recursion logic
        if "tasks" not in root_data:
            log.critical(f"Config file must contain keyword tasks in the root")
            return 1

        pre_process_output = pre_process_tasks(root_data)
        log.debug("Final pre_process_output")
        log.debug(json.dumps(pre_process_output, indent=2))
        log.debug("\n----------------\n")

        parsed_root_data = recurse_build_transactions(pre_process_output)
        log.debug(" -- Final built transactions")
        log.debug(json.dumps(parsed_root_data, indent=2))
        log.debug(" -- transactions for all tickets")
        log.debug(parsed_root_data)
        log.debug("\n")

        # Always start with a blank parent
        recurse_commit_transactions(parsed_root_data, None)
