# -*- coding: utf-8 -*-

"""
Diffusion package for Phabricator repository management.

This package provides the Diffusion class for interacting with Phabricator's
Diffusion (repository hosting) application.

The package is structured into submodules:
- core: Main Diffusion class (orchestrator)
- validators: Input validation functions
- resolvers: Identifier resolution functions
- fetchers: API data fetching functions
- formatters: Data formatting functions
"""

from phabfive.diffusion.core import Diffusion

__all__ = ["Diffusion"]
