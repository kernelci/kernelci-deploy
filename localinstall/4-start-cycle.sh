#!/bin/bash
. ./main.cfg

# is docker-compose exists? if not use docker compose
if [ -z "$(which docker-compose)" ]; then
    echo "docker-compose is not installed, using docker compose"
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

cd kernelci/kernelci-pipeline
${DOCKER_COMPOSE} down
${DOCKER_COMPOSE} up -d

echo "You can view now logs of containers using docker logs -f <container_id> or docker-compose logs -f in kernelci/kernelci-pipeline or kernelci/kernelci-api directories"
echo "Also you can do docker ps to see running containers, and in case of ongoing builds, you can view their logs too by docker logs -f <container_id>"
echo "You can also open API viewer at http://127.0.0.1:8001/viewer"
