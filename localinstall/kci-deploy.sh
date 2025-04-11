#!/bin/bash

set -e

IMAGE_NAME="local/kernelci-deployer:latest"
BUILD_IMAGE=false
ACTION=""
CONTAINER_ARGS=()

function print_help() {
  echo "Usage: $0 [--build] (run|stop) [args...]"
  echo
  echo "Options:"
  echo "  --build     Force rebuild of the Deployer image (optional)"
  echo "  run         Run kernelci deployment (default if no action specified)"
  echo "  stop        Stop and remove kernelci deployment"
  echo "  -h, --help  Show this help message"
  echo
  echo "Arguments after 'run' or 'stop' are passed to the container entrypoint"
  exit 0
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      BUILD_IMAGE=true
      shift
      ;;
    run|stop)
      if [[ -n "$ACTION" ]]; then
        echo "Error: Cannot use both 'run' and 'stop'"
        exit 1
      fi
      ACTION=$1
      shift
      CONTAINER_ARGS=("$@")
      break
      ;;
    -h|--help)
      print_help
      ;;
    *)
      echo "Unknown option: $1"
      print_help
      ;;
  esac
done

# Default
if [[ -z "$ACTION" ]]; then
  ACTION="run"
fi

USER_ID=$(id -u)
GROUP_ID=$(id -g)

if [[ "$BUILD_IMAGE" = true || -z $(docker images -q "$IMAGE_NAME") ]]; then
  echo "Building $IMAGE_NAME"
  docker build \
    --build-arg USER_ID=$USER_ID \
    --build-arg GROUP_ID=$GROUP_ID \
    -f Containerfile \
    -t "$IMAGE_NAME" \
    .
fi

echo "Running $IMAGE_NAME with action '$ACTION' and args: ${CONTAINER_ARGS[*]}"
docker run --rm \
  --name kernelci-deployer \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd)":"$(pwd)" \
  --workdir "$(pwd)" \
  --group-add "$(stat -c '%g' /var/run/docker.sock)" \
  --network host \
  "$IMAGE_NAME" \
  "$ACTION" "${CONTAINER_ARGS[@]}"
