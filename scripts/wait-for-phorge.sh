#!/bin/bash
# Wait until deploy/phorge has rolled out, and fail as soon as its pod cannot
# start rather than when the rollout times out
#
#   KUBE_CONTEXT=k3d-dynamist NAMESPACE=phorge scripts/wait-for-phorge.sh 30m
#
# The entrypoint exits on any error, so a pod in CrashLoopBackOff will not get
# better by waiting: a seed module that does not work on this Phorge version,
# or the guard refusing another version on existing data. Pods being deleted
# are left out, they are the previous rollout's.

set -euo pipefail

timeout=${1:-30m}
kubectl=(mise exec -- kubectl --context "${KUBE_CONTEXT:?}" -n "${NAMESPACE:?}")

"${kubectl[@]}" rollout status deploy/phorge --timeout="$timeout" &
rollout=$!
trap 'kill $rollout 2>/dev/null || true' EXIT

while kill -0 "$rollout" 2>/dev/null; do
  # A pod per line, not being deleted, and its containers' name=reason
  waiting=$("${kubectl[@]}" get pods -l app.kubernetes.io/name=phorge -o jsonpath='{range .items[*]}{.metadata.deletionTimestamp}{"\t"}{range .status.containerStatuses[*]}{.name}={.state.waiting.reason}{" "}{end}{"\n"}{end}' 2>/dev/null |
    awk -F'\t' '$1 == "" { n = split($2, containers, " "); for (i = 1; i <= n; i++) { split(containers[i], c, "="); if (c[2] ~ /^(CrashLoopBackOff|ErrImagePull|ImagePullBackOff|CreateContainerConfigError)$/) { print c[1], c[2]; exit } } }') || true
  if [ -n "$waiting" ]; then
    read -r container reason <<<"$waiting"
    echo "Error: the phorge pod cannot start, container ${container} is in ${reason}, the end of its last run:" >&2
    "${kubectl[@]}" logs deploy/phorge -c "$container" --previous --tail=40 >&2 || true
    exit 1
  fi
  sleep 5
done

wait "$rollout"
