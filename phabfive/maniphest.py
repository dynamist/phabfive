# -*- coding: utf-8 -*-

# python std lib
import json
import logging
from pathlib import Path

# phabfive imports
from phabfive.core import Phabfive
from phabfive.constants import *
from phabfive.exceptions import *

# 3rd party imports
import yaml
from jinja2 import Template


log = logging.getLogger(__name__)

def search_phab(phab_module, *args, **kwargs):
    """
    Helper method that wraps around your search query/endpoint and
    helps you to handle any cursors redirections to gather up all items
    in that search so you do not get stuck on only having access to the first
    page of items.
    """
    results = {"data": []}
    limit = kwargs.get("limit", 100)

    def fetch_results(cursor=None):
        query_params = kwargs.copy()

        if cursor:
            query_params["after"] = cursor

        result = phab_module.search(*args, **query_params)
        results["data"].extend(result.data)

        if len(result.data) >= limit and result["cursor"]["after"] is not None:
            fetch_results(result["cursor"]["after"])

    fetch_results()

    return results["data"]


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
            raise PhabfiveException(f"Must specify a config file path")

        if not Path(config_file).is_file():
            log.error(f"Config file '{config_file}' do not exists")

        with open(config_file) as stream:
            root_data = yaml.load(stream, Loader=yaml.Loader)

        # Fetch all users in phabricator, used by subscribers mapping later
        users_query = search_phab(self.phab.user)
        username_to_id_mapping = {
            user["fields"]["username"]: user["phid"]
            for user in users_query
        }

        log.debug(username_to_id_mapping)

        # Fetch all projects in phabricator, used to map ticket -> projects later
        projects_query = search_phab(self.phab.project, constraints={"name": ""})
        project_name_to_id_map = {
            project["fields"]["name"]: project["phid"]
            for project in projects_query
        }

        log.debug(project_name_to_id_map)

        # Gather and remove variables to avoid using it or polluting the data later on
        variables = root_data["variables"]
        del root_data["variables"]

        # Helper lambda to slim down transaction handling
        add_transaction = lambda t, transaction_type, value : t.append(
            {"type": transaction_type, "value": value},
        )

        def jinja_render(data_block, variable_name, variables):
            """
            Helper method to simplify Jinja2 rendering of a given value to a set of variables
            """
            data = data_block.get(variable_name, None)

            if data:
                data_block[variable_name] = Template(data).render(variables)

        def pre_process_tasks(task_config):
            """
            This is the main parser that can be run recurse in order to sort out an individual ticket and recurse down
            to pre process each task and to query all internal ID:s and update the datastructure
            """
            log.debug("Pre processing tasks")
            log.debug(task_config)

            output = task_config.copy()

            # Render strings that should be possible to render with Jinja2
            jinja_render(output, "title", variables)
            jinja_render(output, "description", variables)
            jinja_render(output, "owner", variables)

            # Validate and translate project names to internal project PHID:s
            project_phids = []

            for project_name in output.get("projects", []):
                project_phid = project_name_to_id_map.get(project_name, None)

                if not project_phid:
                    raise PhabfiveRemoteException(f"Project '{project_name}' is not found on the phabricator server")

                project_phids.append(project_phid)

            output["projects"] = project_phids

            # Validate and translate all subscriber users to PHID:s
            user_phids = []

            for subscriber_name in output.get("subscribers", []):
                log.debug(f"processing user {subscriber_name}")
                user_phid = username_to_id_mapping.get(subscriber_name, None)

                if not user_phid:
                    raise PhabfiveRemoteException(f"Subscriber '{subscriber_name}' not found as a user on the phabricator server")

                user_phids.append(user_phid)

            # Remap owner name to valid PHID
            owner = output.get("owner", None)
            owner_phid = username_to_id_mapping.get(owner, None)

            if owner:
                output["owner"] = owner_phid

            # Recurse down and process all child tasks
            processed_child_tasks = []
            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                processed_child_tasks = [
                    pre_process_tasks(task)
                    for task in child_tasks
                ]

            output["tasks"] = processed_child_tasks

            return output

        def recurse_build_transactions(task_config):
            """
            This block recurses over all tasks and builds the transaction set for this ticket and stores it
            in the data structure.
            """
            log.debug(f"Building transactions for task_config")
            log.debug(task_config)

            # In order to not cause issues with injecting data in a recurse traversal, copy the input,
            # modify the data and return data that is later used to build a new full data structure
            output = task_config.copy()

            transactions = []

            if "title" in task_config and "description" in task_config:
                add_transaction(transactions, "title", task_config["title"])
                add_transaction(transactions, "description", task_config["description"])
                add_transaction(transactions, "priority", task_config.get("priority", TICKET_PRIORITY_NORMAL))

                owner = task_config.get("owner", None)
                if owner:
                    add_transaction(transactions, "owner", owner)

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
                            constraints={"ids": [int(ticket_id[1:])]},
                        )

                        if len(search_result["data"]) != 1:
                            raise PhabfiveRemoteException(f"Unable to find subtask ticket in phabricator instance with ID={ticket_id}")

                        subtasks_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "subtasks.set", subtasks_phids)

                parents = task_config.get("parents", [])

                if parents:
                    parent_phids = []

                    for ticket_id in parents:
                        search_result = self.phab.maniphest.search(
                            constraints={"ids": [int(ticket_id[1:])]},
                        )

                        if len(search_result["data"]) != 1:
                            raise PhabfiveRemoteException(f"Unable to find parent ticket in phabricator instance with ID={ticket_id}")

                        parent_phids.append(search_result["data"][0]["phid"])

                    add_transaction(transactions, "parents.set", parent_phids)
            else:
                log.warning("Required fields 'title' and 'description' is not present in this data block, skipping ticket creation")

            output["transactions"] = transactions

            processed_child_tasks = []
            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                # If there is child tasks to create, recurse down to all of them one by one
                processed_child_tasks = [
                    recurse_build_transactions(task)
                    for task in child_tasks
                ]
            else:
                processed_child_tasks = []

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

            if transactions_to_commit:
                # Parent ticket based on the task hiearchy defined in the config file we parsed is different
                # from the explicit "ticket parent" that can be defined 
                if parent_task_config and "phid" in parent_task_config:
                    add_transaction(transactions_to_commit, "parents.add", [parent_task_config["phid"]])

                log.debug(" -- transactions to commit")
                log.debug(transactions_to_commit)

                if dry_run:
                    log.critical("Running with --dry-run, tickets !!WILL NOT BE!! commited to phabricator")
                else:
                    result = self.phab.maniphest.edit(
                        transactions=transactions_to_commit,
                    )

                    # Store the newly created ticket ID in the data structure so child tickets can look it up
                    task_config["id"] = str(result["object"]["id"])
                    task_config["phid"] = str(result["object"]["phid"])
            else:
                log.warning("No transactions to commit here, either a bug or root object that can't be transacted")

            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                for child_task in child_tasks:
                    recurse_commit_transactions(child_task, task_config)

        def recurse_ticket_report(task_config):
            """
            Recursive method that walks through the entire processed task tree
            and prints a user-friendly report back to the user about what tasks was
            created in the phabricator server
            """
            ticket_id = task_config.get("id")

            # If we are at the root level, the ID won't be set, but we have
            # child tasks to parse through
            if ticket_id:
                _, ticket_info = self.info(int(ticket_id))
                title = ticket_info['title']
                tid = ticket_info['id']
                uri = ticket_info['uri']

                print("{:<8} {:<30} {:<12}".format(tid, title[:30], uri))

            child_tasks = task_config.get("tasks", None)

            if child_tasks:
                for child_task in child_tasks:
                    recurse_ticket_report(child_task)

        # Main task recursion logic
        if "tasks" not in root_data:
            raise PhabfiveDataException(f"Config file must contain keyword tasks in the root")

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

        if dry_run:
            log.warning(f"Dry run mode, no tickets created so nothing to report")
        else:
            print("\nList of created tickets")
            print("----------------------")
            print("{:<8} {:<30} {:<12}".format("ID", "Title", "URI"))
            recurse_ticket_report(parsed_root_data)
