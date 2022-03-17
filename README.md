# phabfive

A command line tool to interact with Phabricator.

The complete documentation for Phabfive can be found at [Read the Docs](https://phabfive.readthedocs.io/en/latest/)


## Features

A summary of the currently supported features:

- Passphrase
  - Get specified secret
- Diffusion
  - List repositories
  - Get branches for specified repository
  - Get clone URI for specified repository
  - Add repository
  - Edit URI
  - Create URI (observe repository)
- Paste
  - List pastes
  - Get specified paste
  - Add paste
- User
  - Who am I: information about the logged-in user


## Example usage

Grab a Phabricator token at `https://<yourserver.com>/settings/panel/apitokens/`

Configure:

     export PHAB_TOKEN=cli-ABC123
    --OR--
     echo "PHAB_TOKEN: cli-ABC123" > ~/.config/phabfive.yaml

Usage:

    phabfive passphrase K123


## LICENSE

Copyright (c) 2017-2019 Dynamist AB

See the LICENSE file provided with the source distribution for full details.
