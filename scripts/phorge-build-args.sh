#!/bin/bash
# Print the docker build arguments for a Phorge VERSION (see phorge/Dockerfile)
#
#   stable, master   a branch, at its current commit
#   2025.51          a release tag
#
# Any branch or tag works, as long as Phorge and Arcanist both have it, which
# upstream keeps true for its branches and release tags. The commits are
# resolved here rather than in the build, so Docker's cache only goes stale
# when upstream actually moved.

set -euo pipefail

version=${1:?usage: $0 VERSION}

log() {
  echo "$*" >&2
}

# The commit of a branch or tag, a peeled tag winning over the tag object
commit() {
  local repo=$1 refs sha
  refs=$(git ls-remote "https://github.com/phorgeit/${repo}.git" \
    "refs/heads/${version}" "refs/tags/${version}" "refs/tags/${version}^{}")
  sha=$(awk -v tag="refs/tags/${version}" '
      $2 == tag "^{}" { peeled = $1 }
      $2 == tag { tagged = $1 }
      $2 ~ /^refs\/heads\// { branch = $1 }
      END { print branch ? branch : peeled ? peeled : tagged }' <<<"$refs")
  if [ -z "$sha" ]; then
    log "Error: no branch or tag ${version} in phorgeit/${repo}, use e.g. stable, master or 2025.51"
    exit 1
  fi
  echo "$sha"
}

phorge=$(commit phorge)
arcanist=$(commit arcanist)
log "Phorge ${version} at ${phorge}, Arcanist at ${arcanist}"
echo "--build-arg PHORGE_VERSION=${version} --build-arg PHORGE_COMMIT=${phorge} --build-arg ARCANIST_COMMIT=${arcanist}"
