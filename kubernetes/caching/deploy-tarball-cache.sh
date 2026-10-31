#!/bin/bash
# Deploy the source tarball pull-through cache (tarball-cache.yaml) to the
# external build clusters.  See README.md.
#
# ADDITIVE ONLY.  This script never deletes anything: it only runs
# "kubectl apply" (no --prune, no --force, no replace) on the objects in
# tarball-cache.yaml, all of which are new names (tarball-cache,
# tarball-cache-nginx).  Before applying it refuses to continue if an
# object with one of those names already exists without our
# app.kubernetes.io/managed-by label, so it cannot take over or modify
# anything it did not create.  The existing caching setup (shared-data,
# nfs-server, file-sync) is not touched.
#
# Usage:
#   ./deploy-tarball-cache.sh check  [ctx...]   read-only: access, name clashes, server dry-run
#   ./deploy-tarball-cache.sh diff   [ctx...]   read-only: kubectl diff against the live cluster
#   ./deploy-tarball-cache.sh status [ctx...]   read-only: objects, pod, cache HIT/MISS counts
#   ./deploy-tarball-cache.sh apply  ctx...|--all   check, then kubectl apply
#
# Without contexts, read-only commands run on all clusters in CONTEXTS.
# "apply" needs explicit contexts (or --all) so rollout can be staged.
#
# GKE clusters are only reachable through the kernelci-staging jumphost and
# with the kernelci service account.  Before running, open the tunnel:
#   ssh -fN -D 127.0.0.1:18080 kernelci-staging
# Defaults below can be overridden from the environment.
GKE_PROXY=${GKE_PROXY:-socks5://127.0.0.1:18080}
GKE_CLOUDSDK_CONFIG=${GKE_CLOUDSDK_CONFIG:-$HOME/.config/gcloud_kernelci}
# Keep this kubeconfig in its own directory: gke-gcloud-auth-plugin caches
# its token next to the kubeconfig and would otherwise reuse another account
GKE_KUBECONFIG=${GKE_KUBECONFIG:-$HOME/.kube/gke-kernelci/config}

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
MANIFEST="$HERE/tarball-cache.yaml"
LABEL_VAL=kernelci-deploy-tarball-cache
NS=default
OBJECTS="configmap/tarball-cache-nginx persistentvolumeclaim/tarball-cache deployment/tarball-cache service/tarball-cache"

# Clusters that pay for source tarball egress.  aks-kbuild-medium-1 is not
# listed on purpose: it is in westus3 like the prod VM, so its downloads
# are free and a cache would only add cost.
CONTEXTS=(
  gke_android-kernelci-external_europe-west1-d_kci-eu-west1
  gke_android-kernelci-external_europe-west4-c_kci-eu-west4
  gke_android-kernelci-external_us-central1-c_kci-us-central1
  gke_android-kernelci-external_us-east4-c_kci-big-us-east4
  gke_android-kernelci-external_us-west1-a_kci-us-west1
  aks-kbuild-scus-1
)

# Storage class for the cache PVC, per cloud (see tarball-cache.yaml)
GKE_STORAGE_CLASS=standard-rwo

# Manifest for one cluster on stdout: fills in the #STORAGECLASS# marker
manifest() {
  local ctx=$1
  if [[ $ctx == gke_* ]]; then
    sed "s|^  #STORAGECLASS#\$|  storageClassName: $GKE_STORAGE_CLASS|" "$MANIFEST"
  else
    sed '/^  #STORAGECLASS#$/d' "$MANIFEST"
  fi
}

k() {
  local ctx=$1; shift
  if [[ $ctx == gke_* ]]; then
    HTTPS_PROXY=$GKE_PROXY CLOUDSDK_CONFIG=$GKE_CLOUDSDK_CONFIG \
      KUBECONFIG=$GKE_KUBECONFIG timeout 120 kubectl --context="$ctx" "$@"
  else
    timeout 120 kubectl --context="$ctx" "$@"
  fi
}

# Fail if any of our object names exists without our label
check_ownership() {
  local ctx=$1 obj owner rc=0
  for obj in $OBJECTS; do
    if owner=$(k "$ctx" -n "$NS" get "$obj" --ignore-not-found \
                 -o "jsonpath={.metadata.labels.app\.kubernetes\.io/managed-by}{'|found'}"); then
      if [[ -n $owner && $owner != "$LABEL_VAL|found" ]]; then
        echo "  REFUSE: $obj exists in $ctx and is not managed by $LABEL_VAL" >&2
        rc=1
      fi
    else
      echo "  ERROR: cannot query $obj in $ctx" >&2
      return 1
    fi
  done
  return $rc
}

do_check() {
  local ctx=$1
  echo "== check $ctx"
  k "$ctx" -n "$NS" auth can-i create deployments >/dev/null || {
    echo "  ERROR: no access to $ctx" >&2; return 1; }
  check_ownership "$ctx" || return 1
  manifest "$ctx" | k "$ctx" apply --dry-run=server -f - | sed 's/^/  /' || return 1
}

do_diff() {
  local ctx=$1 rc=0
  echo "== diff $ctx"
  manifest "$ctx" | k "$ctx" diff -f - || rc=$?
  # kubectl diff: 0 = no changes, 1 = changes shown, >1 = error
  [[ $rc -le 1 ]] || return $rc
}

do_status() {
  local ctx=$1
  echo "== status $ctx"
  k "$ctx" -n "$NS" get $OBJECTS --ignore-not-found || return 1
  k "$ctx" -n "$NS" get pods -l app.kubernetes.io/name=tarball-cache -o wide 2>/dev/null || true
  if k "$ctx" -n "$NS" get deployment/tarball-cache >/dev/null 2>&1; then
    echo "  cache results since pod start:"
    k "$ctx" -n "$NS" logs deployment/tarball-cache --tail=-1 2>/dev/null \
      | grep -o 'cache=[A-Z-]*' | sort | uniq -c | sed 's/^/  /' || true
  fi
}

do_apply() {
  local ctx=$1
  do_check "$ctx" || return 1
  echo "== apply $ctx"
  manifest "$ctx" | k "$ctx" apply -f - | sed 's/^/  /' || return 1
  k "$ctx" -n "$NS" rollout status deployment/tarball-cache --timeout=300s
}

cmd=${1:-}; shift || true
case $cmd in
  check|diff|status) ;;
  apply)
    if [[ $# -eq 0 ]]; then
      echo "apply needs explicit contexts or --all" >&2; exit 2
    fi ;;
  *) sed -n '2,/^GKE_PROXY/p' "$0" | sed '$d'; exit 2 ;;
esac

if [[ ${1:-} == --all || $# -eq 0 ]]; then
  targets=("${CONTEXTS[@]}")
else
  targets=("$@")
fi

# Note: functions run below as "f || failed+=", where set -e is off, so
# every step inside them propagates failure with an explicit "|| return 1".
failed=()
for ctx in "${targets[@]}"; do
  "do_$cmd" "$ctx" || failed+=("$ctx")
done
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "FAILED: ${failed[*]}" >&2
  exit 1
fi
