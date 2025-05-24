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

# down, just in case old containers are running
docker compose down
docker compose up -d
echo "Waiting for API to be up"
sleep 5
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

