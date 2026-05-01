# -*- coding: utf-8 -*-
"""YAML utilities for phabfive."""

import sys
from collections import defaultdict

from ruamel.yaml import YAML


def parse_yaml_from_stdin(parse_monogram_func):
    """Parse YAML documents from stdin.

    Args:
        parse_monogram_func: Function to parse monograms (e.g., Phabfive.parse_monogram)

    Returns:
        list: List of dicts, each containing:
              - object_type: str ("task"|"passphrase"|"paste")
              - object_id: str (numeric ID)
              - data: dict (parsed YAML data)

    Raises:
        ValueError: If YAML is invalid or missing required fields
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False

    objects = []

    # Read all YAML documents from stdin
    try:
        for doc in yaml.load_all(sys.stdin):
            if doc is None:
                continue

            # Extract monogram from Link field
            if "Link" not in doc:
                raise ValueError("YAML document missing 'Link' field")

            link = doc["Link"]
            object_type, object_id = parse_monogram_func(link)

            objects.append(
                {"object_type": object_type, "object_id": object_id, "data": doc}
            )

    except Exception as e:
        raise ValueError(f"Failed to parse YAML from stdin: {e}")

    return objects


def group_objects_by_type(objects):
    """Group objects by their type.

    Args:
        objects (list): List of object dicts from parse_yaml_from_stdin

    Returns:
        dict: Dict mapping object_type -> list of objects
    """
    grouped = defaultdict(list)
    for obj in objects:
        grouped[obj["object_type"]].append(obj)
    return dict(grouped)
