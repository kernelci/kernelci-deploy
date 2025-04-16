#!/bin/bash

set -e

ACTION=$1

function print_help() {
  echo "Usage: $0 (deploy|start|stop)"
  echo
  echo "  deploy  Configure and start deployment"
  echo "  start   Start deployment"
  echo "  stop    Stop deployment"
  exit 1
}

function check_deploy() {
  if [ ! -f kernelci/.done ]; then
    echo "Error: Deployment not completed. Please run 'deploy' first."
    exit 1
  fi
}

if [[ -z "$ACTION" ]]; then
  echo "Error: Missing required action (deploy, start or stop)"
  print_help
fi

case "$ACTION" in
  deploy)
    rm -f kernelci/.done
    echo "Starting deployment sequence, this may take a while..."
    ./scripts/1-rebuild_all.sh
    ./scripts/2-prepare_api.sh
    ./scripts/3-start_api.sh
    ./scripts/4-set_api_admin.sh
    ./scripts/5-prepare_pipeline.sh
    ./scripts/6-start_pipeline.sh
    touch kernelci/.done
    ;;
  start)
    check_deploy
    echo "Starting deployment"
    ./scripts/3-start_api.sh
    ./scripts/6-start_pipeline.sh
    ;;
  stop)
    check_deploy
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
