#!/bin/bash

. ./config/main.cfg

set -e

# i am groot?
if [ $(id -u) -ne 0 ]; then
    SUDO=sudo
else
    SUDO=
fi

cp config/.env-api kernelci/kernelci-api/.env
cp config/api-configs.yaml kernelci/kernelci-core/config/core/
cp config/kernelci-cli.toml kernelci/kernelci-core/kernelci.toml

sed -i "s/#SECRET_KEY=/SECRET_KEY=${API_SECRET_KEY}/" kernelci/kernelci-api/.env

cd kernelci/kernelci-api
mkdir -p docker/redis/data
${SUDO} chmod -R 0777 docker/storage/data
${SUDO} chmod -R 0777 docker/redis/data
# enable ssh and storage nginx
mkdir -p ../../config/out
sed -i 's/^#  /  /' docker-compose.yaml
if [ -f ../../config/out/ssh.key ]; then
    echo "ssh.key already exists"
else
    # generate non-interactively ssh key to ssh.key
    ssh-keygen -t rsa -b 4096 -N "" -f ../../config/out/ssh.key
fi
# get public key and add to docker/ssh/user-data/authorized_keys
cat ../../config/out/ssh.key.pub > docker/ssh/user-data/authorized_keys
