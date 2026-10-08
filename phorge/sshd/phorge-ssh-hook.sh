#!/bin/sh
# AuthorizedKeysCommand: sshd asks which keys may log in as $1, and Phorge's
# ssh-auth answers with every active key, each forced to run ssh-exec as its
# owner. sshd requires this file and its directories to be owned by root.

VCSUSER="git"
ROOT="/app/phorge"

if [ "$1" != "$VCSUSER" ]; then
  exit 1
fi

# sshd runs this with an empty environment, and ssh-auth starts with
# `#!/usr/bin/env php`, which lives in /usr/local/bin in the php images
export PATH=/usr/local/bin:/usr/bin:/bin

exec "$ROOT/bin/ssh-auth" "$@"
