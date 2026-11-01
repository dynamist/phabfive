#!/bin/bash
# Put the local checkouts mounted at /src/NAME into /app/NAME as they are on disk,
# for `make phorge-image VERSION=local` (see phorge/Dockerfile). Each is a shallow
# clone of the checkout's HEAD, so git still says which commit it is at, holding
# the files of the working tree, committed or not. Files the checkout's
# .gitignore excludes, like Phorge's conf/local/local.json, stay out.
#
#   checkout-local.sh phorge arcanist

set -euo pipefail

# comm needs both lists sorted the same way
export LC_ALL=C

# The checkouts may belong to whoever built the image rather than to root, so
# git needs them named as safe: the checkout, and its .git, which is what the
# upload-pack of a clone checks. That upload-pack does not see -c options, so
# they go in a global config of this script's own.
GIT_CONFIG_GLOBAL=$(mktemp)
export GIT_CONFIG_GLOBAL
trap 'rm -f "$GIT_CONFIG_GLOBAL"' EXIT
for name in "$@"; do
  git config --global --add safe.directory "/src/$name"
  git config --global --add safe.directory "/src/$name/.git"
done

for name in "$@"; do
  src=/src/$name
  dst=/app/$name

  # A git worktree has a .git file pointing outside the build, and fails here
  git clone -q --depth 1 --no-checkout "file://$src" "$dst"

  # Every tracked or untracked file, less the ones deleted from the working tree
  comm -z -23 \
    <(git -C "$src" ls-files -z --cached --others --exclude-standard | sort -z) \
    <(git -C "$src" ls-files -z --deleted | sort -z) |
    tar -c -f - --null -C "$src" -T - |
    tar -x -f - -C "$dst"

  # Index HEAD, so git status in the image shows what was not committed, and
  # keep that list from before the pod adds files of its own (banner.sh)
  git -C "$dst" reset -q
  git -C "$dst" status --porcelain --untracked-files=all >"$dst/.git/phabfive-uncommitted"
done
