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
#
# PHP is the newest series the version starts and seeds on, see php_for, or
# the second argument when given.

set -euo pipefail

version=${1:?usage: $0 VERSION [PHP]}
php=${2:-}

log() {
  echo "$*" >&2
}

# Whether release $1 is older than release $2
older() {
  [ "$1" != "$2" ] && [ "$(printf '%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

# Phorge turns a deprecation raised while it runs into an error, so a release
# only works on the PHP series its code predates. Found by starting each
# release on a fresh database: 2023.23 and older call strlen() with null
# (deprecated in 8.1), 2024.35 and older have implicitly nullable parameters
# (8.4), 2025.18 has `case ...;` (8.5). A branch, or a newer release, runs on
# the newest.
php_for() {
  if ! [[ $1 =~ ^[0-9]{4}\.[0-9]+$ ]]; then
    echo 8.5
  elif older "$1" 2023.32; then
    echo 8.0
  elif older "$1" 2025.18; then
    echo 8.3
  elif older "$1" 2025.51; then
    echo 8.4
  else
    echo 8.5
  fi
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
php=${php:-$(php_for "$version")}
log "Phorge ${version} at ${phorge}, Arcanist at ${arcanist}, on PHP ${php}"
echo "--build-arg PHORGE_VERSION=${version} --build-arg PHORGE_COMMIT=${phorge} --build-arg ARCANIST_COMMIT=${arcanist} --build-arg PHP=${php}"
