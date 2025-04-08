#!/bin/sh
. ./main.cfg

# is docker-compose exists? if not use docker compose
if [ -z "$(which docker-compose)" ]; then
    echo "docker-compose is not installed, using docker compose"
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

# i am groot?
if [ $(id -u) -ne 0 ]; then
    SUDO=sudo
else
    SUDO=
fi

cp .env-api kernelci/kernelci-api/.env
cp api-configs.yaml kernelci/kernelci-core/config/core/
cp kernelci-cli.toml kernelci/kernelci-core/kernelci.toml

sed -i "s/#SECRET_KEY=/SECRET_KEY=${API_SECRET_KEY}/" kernelci/kernelci-api/.env

cd kernelci/kernelci-api
mkdir -p docker/redis/data
${SUDO} chmod -R 0777 docker/storage/data
${SUDO} chmod -R 0777 docker/redis/data
# enable ssh and storage nginx
sed -i 's/^#  /  /' docker-compose.yaml
if [ -f ../../ssh.key ]; then
    echo "ssh.key already exists"
else
    # generate non-interactively ssh key to ssh.key
    ssh-keygen -t rsa -b 4096 -N "" -f ../../ssh.key
fi
# get public key and add to docker/ssh/user-data/authorized_keys
cat ../../ssh.key.pub > docker/ssh/user-data/authorized_keys

# down, just in case old containers are running
${DOCKER_COMPOSE} down
${DOCKER_COMPOSE} up -d
echo "Waiting for API to be up"
sleep 1
# loop until the API is up, try 5 times
i=0
while [ $i -lt 5 ]; do
    ANSWER=$(curl http://localhost:8001/latest/)
    # must be {"message":"KernelCI API"}
    if [ "$ANSWER" != "{\"message\":\"KernelCI API\"}" ]; then
        echo "API is not up"
        i=$((i+1))
        sleep 5
    else
        echo "API is up"
        break
    fi
done

# check for expect
if [ -z "$(which expect)" ]; then
    echo "expect is not installed, please install it"
    exit 1
fi

# INFO, if you have issues with stale/old data, check for 
# docker volume kernelci-api_mongodata and delete it
../../helpers/scripts_setup_admin_user.exp "${YOUR_EMAIL}" "${ADMIN_PASSWORD}"

cd ../kernelci-core
echo "Issuing token for admin user"
../../helpers/kci_user_token_admin.exp "${ADMIN_PASSWORD}" > ../../admin-token.txt
ADMIN_TOKEN=$(cat ../../admin-token.txt | tr -d '\r\n')

echo "[kci.secrets]
api.\"docker-host\".token = \"$ADMIN_TOKEN\" 
" >> kernelci.toml

