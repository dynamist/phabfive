# phabfive

[![Travis CI](https://travis-ci.com/dynamist/phabfive.svg?branch=master)](https://travis-ci.com/dynamist/phabfive)
[![Current release](https://img.shields.io/github/v/release/dynamist/phabfive.svg)](https://github.com/dynamist/phabfive/releases)
[![GitHub issues](https://img.shields.io/github/issues/dynamist/phabfive.svg?maxAge=360)](https://github.com/dynamist/phabfive/issues)
[![License](https://img.shields.io/github/license/dynamist/phabfive?color=%23fe0000)](https://github.com/dynamist/phabfive/blob/master/LICENSE)
[![Python2](https://img.shields.io/badge/python2-2.7-blue.svg)](https://github.com/dynamist/phabfive/)
[![Python3](https://img.shields.io/badge/python3-3.6,3.7,3.8-blue.svg)](https://github.com/dynamist/phabfive/)

A command line tool to interact with Phabricator.

The complete documentation and detailed documentation of all implemented commands for Phabfive can be found at [Read the Docs](https://phabfive.readthedocs.io/en/latest/)


## Basic example

Grab a Phabricator token at https://<yourserver.com>/settings/panel/apitokens/

Configure:

     export PHAB_TOKEN=cli-ABC123
    --OR--
     echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml

Usage:

    phabfive passphrase K123

More detailed examples can be found on the [Read the Docs](https://phabfive.readthedocs.io/en/latest/) website


## LICENSE

Copyright (c) 2017-2020 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
