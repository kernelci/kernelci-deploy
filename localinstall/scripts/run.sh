#!/bin/bash

set -e

ACTION=$1

function print_help() {
  echo "Usage: $0 (run|stop)"
  echo
  echo "  run   Execute deployment sequence"
  echo "  stop  Stop and remove deployment"
  exit 1
}

if [[ -z "$ACTION" ]]; then
  echo "Error: Missing required action (run or stop)"
  print_help
fi

case "$ACTION" in
  run)
    echo "Starting deployment sequence, this may take a while..."
    ./scripts/1-rebuild_all.sh
    ./scripts/2-install_api.sh
    ./scripts/3-install_pipeline.sh
    ./scripts/4-start_cycle.sh
    ;;
  stop)
    echo "Stopping deployment"
    cd kernelci/kernelci-api
    docker compose down
    cd ../kernelci-pipeline
    docker compose down
    ;;
  *)
    echo "Error: Invalid action '$ACTION'"
    print_help
    ;;
esac
