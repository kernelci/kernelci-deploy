#!/bin/bash

. ./config/main.cfg

set -e

# i am groot?
if [ $(id -u) -ne 0 ]; then
    SUDO=sudo
else
    SUDO=
fi

cd kernelci/kernelci-api

# check for expect
if [ -z "$(which expect)" ]; then
    echo "expect is not installed, please install it"
    exit 1
fi

# INFO, if you have issues with stale/old data, check for 
# docker volume kernelci-api_mongodata and delete it
expect ../../helpers/scripts_setup_admin_user.exp "${YOUR_EMAIL}" "${ADMIN_PASSWORD}"

cd ../kernelci-core
echo "Issuing token for admin user"
expect ../../helpers/kci_user_token_admin.exp "${ADMIN_PASSWORD}" > ../../config/out/admin-token.txt
ADMIN_TOKEN=$(cat ../../config/out/admin-token.txt | tr -d '\r\n')

echo "[kci.secrets]
api.\"docker-host\".token = \"$ADMIN_TOKEN\" 
" >> kernelci.toml