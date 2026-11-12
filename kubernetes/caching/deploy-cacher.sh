#!/bin/bash
# Deploy the shared HTTP cache. The old NFS provisioning entry point is retired.
# See README.md for the firmware client rollout and legacy-resource retirement.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
case ${1:-} in
  render|check|diff|status|apply)
    exec "$HERE/deploy-tarball-cache.sh" "$@"
    ;;
  fetch|update)
    echo "Legacy NFS refresh/provisioning is retired from this entry point." >&2
    echo "Use deploy-tarball-cache.sh; see README.md for existing /data clients." >&2
    exit 2
    ;;
  *)
    echo "Usage: $0 {render|check|diff|status|apply} [context...|--all]" >&2
    echo "Firmware and source tarballs use tarball-cache; see README.md for migration." >&2
    exit 2
    ;;
esac
