# -*- coding: utf-8 -*-
"""When the command started, taken before it imports anything heavy.

phabfive/cli/__init__.py imports this first, so a recorded run's profile
starts here and the time spent importing typer, rich and the commands shows
up as its start-up phase.
"""

import time

STARTED_WALL = time.time()
STARTED_PERF = time.perf_counter()
