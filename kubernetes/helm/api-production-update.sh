#!/usr/bin/env bash
set -euo pipefail

# Build/publish KernelCI images, then update the existing Helm releases.
# SPDX-License-Identifier: LGPL-2.1-only

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

# This updater uses Docker's registry credentials, not deployment credentials.
clear_deployment_secrets

usage() {
    cat <<EOF
Usage: $0 [workflow]

Without arguments, clone Core/API/Pipeline and build and push their Docker images.
With workflow, skip builds and use the already published images.
Both modes fetch fresh Pipeline configuration and update the existing Helm releases
through deploy.sh update. Settings come from the selected environment's deployment.yaml.
Requires Git, Docker, kubectl, Helm, Python 3, and Mike Farah yq v4.
Initial deployment must already have been performed with deploy.sh full.
EOF
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    ''|workflow) ;;
    *) usage >&2; exit 1 ;;
esac
(( $# <= 1 )) || { usage >&2; exit 1; }

load_deployment_config
for command in git docker kubectl helm python3 yq; do
    command -v "${command}" >/dev/null 2>&1 || { echo "Required command not found: ${command}" >&2; exit 1; }
done
[[ "$(yq --version)" == *'version v4.'* ]] || { echo "Mike Farah yq v4 is required" >&2; exit 1; }

# Validate the target before building or pushing anything.
bash "${SCRIPT_DIR}/deploy.sh" show-deployments
helm status kernelci-api --kube-context "${CONTEXT}" -n "${NS_API}" >/dev/null
helm status kernelci-pipeline --kube-context "${CONTEXT}" -n "${NS_PIPELINE}" >/dev/null

WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "${WORK_DIR}"' EXIT

clone_repo() {
    git clone --branch "$2" --single-branch "$1" "${WORK_DIR}/$3"
}

build_images() {
    local core_rev api_rev pipeline_rev
    clone_repo "${CORE_REPO_URL}" "${CORE_BRANCH}" kernelci-core
    clone_repo "${API_REPO_URL}" "${API_BRANCH}" kernelci-api
    core_rev="$(git -C "${WORK_DIR}/kernelci-core" rev-parse HEAD)"
    api_rev="$(git -C "${WORK_DIR}/kernelci-api" rev-parse HEAD)"
    pipeline_rev="$(git -C "${WORK_DIR}/kernelci-pipeline" rev-parse HEAD)"
    echo "Building images: core=${core_rev} api=${api_rev} pipeline=${pipeline_rev}"
    # As in the legacy updater, use Core's kci builder and the host Docker daemon.
    docker run --rm --privileged \
        -v "${WORK_DIR}/kernelci-core:/kernelci-core:ro" -w /kernelci-core \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "${HOME}/.docker:/root/.docker" python:3.11 bash -euc '
            # Keep pip/build output inside the container, avoiding root-owned host files.
            build_dir="$(mktemp -d)"
            cp -a . "$build_dir/core"
            cd "$build_dir/core"
            python3 -m pip install ".[dev]"
            for component in base api pipeline lava-callback; do
                targets=(kernelci)
                [[ "$component" == base ]] || targets+=("$component")
                ./kci docker build --build-arg "core_rev=$1" --build-arg "api_rev=$2" \
                    --build-arg "pipeline_rev=$3" --prefix="$4" -v --push "${targets[@]}"
            done
        ' -- "${core_rev}" "${api_rev}" "${pipeline_rev}" "${IMAGE_PREFIX}"
}

image_digest() {
    local tag="$1" reference="${IMAGE_PREFIX}kernelci:$1" digest created
    docker pull "${reference}" >&2 || return 1
    # Inspect RepoDigests rather than parsing Docker's human-readable pull output.
    digest="$(docker image inspect "${reference}" --format '{{range .RepoDigests}}{{println .}}{{end}}' |
        while IFS= read -r entry; do
            if [[ "${entry%%@*}" == "${IMAGE_PREFIX}kernelci" ]]; then
                printf '%s\n' "${entry#*@}"
                break
            fi
        done)" || return 1
    [[ "${digest}" =~ ^sha256:[a-f0-9]{64}$ ]] || {
        echo "Could not resolve image digest for ${reference}" >&2
        return 1
    }
    created="$(docker image inspect "${reference}" --format '{{.Created}}')" || return 1
    echo "${tag}: ${digest}; created ${created}" >&2
    printf '%s\n' "${digest}"
}

# Use the same Pipeline revision for image build arguments and configuration.
clone_repo "${PIPELINE_REPO_URL}" "${PIPELINE_BRANCH}" kernelci-pipeline
if [[ "${1:-}" != workflow ]]; then
    build_images
fi

API_IMAGE_DIGEST="$(image_digest api)"
PIPELINE_IMAGE_DIGEST="$(image_digest pipeline)"
LAVA_CALLBACK_IMAGE_DIGEST="$(image_digest lava-callback)"
export API_IMAGE_DIGEST PIPELINE_IMAGE_DIGEST LAVA_CALLBACK_IMAGE_DIGEST
export UPDATE_IMAGE_REPOSITORY="${IMAGE_PREFIX}kernelci"
PIPELINE_CHECKOUT_DIR="${WORK_DIR}/kernelci-pipeline" bash "${SCRIPT_DIR}/deploy.sh" update
echo "[UPDATE] API and Pipeline Helm upgrades completed"
