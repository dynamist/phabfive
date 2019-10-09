#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import phabfive

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

# Allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

README = "README.md"
CHANGELOG = "CHANGELOG.md"

install_requires = ["anyconfig", "appdirs", "phabricator", "pyyaml", "docopt"]
tests_require = ["coverage", "flake8", "pytest", "tox"]
download_url = "{}/tarball/v{}".format(
    "https://github.com/dynamist/phabfive", phabfive.__version__
)

setup(
    name=phabfive.__name__,
    version=phabfive.__version__,
    description=phabfive.__doc__,
    long_description=README + "\n\n" + CHANGELOG,
    author="Rickard Eriksson",
    author_email="rickard@dynamist.se",
    url=phabfive.__url__,
    download_url=download_url,
    zip_safe=False,  # Prevent creation of egg
    install_requires=install_requires,
    tests_require=tests_require,
    extras_require={"test": tests_require},
    packages=["phabfive"],
    entry_points={"console_scripts": ["phabfive = phabfive.cli:cli_entrypoint"]},
    classifiers=[
        # 'Development Status :: 1 - Planning',
        "Development Status :: 2 - Pre-Alpha",
        # 'Development Status :: 3 - Alpha',
        # 'Development Status :: 4 - Beta',
        # 'Development Status :: 5 - Production/Stable',
        # 'Development Status :: 6 - Mature',
        # 'Development Status :: 7 - Inactive',
        "Intended Audience :: System Administrators",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Environment :: Console",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.6",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
    ],
    platforms=["OS Independent"],
    license="Apache License 2.0",
)
