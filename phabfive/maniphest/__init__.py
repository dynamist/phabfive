# -*- coding: utf-8 -*-

"""
Maniphest package for Phabricator task management.

This package provides the Maniphest class for interacting with Phabricator's
Maniphest (task tracking) application.

The package is structured into submodules:
- core: Main Maniphest class (orchestrator)
- resolvers: PHID resolution functions
- fetchers: API data fetching functions
- validators: Input validation functions
- filters: Task filtering functions
- formatters: Data formatting functions
- utils: Utility functions (timestamps, variable rendering)
"""

from phabfive.maniphest.core import Maniphest

__all__ = ["Maniphest"]
