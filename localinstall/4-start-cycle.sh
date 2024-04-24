#!/bin/bash
. ./main.cfg

cd kernelci/kernelci-pipeline
docker-compose down
docker-compose up -d
echo "You can view now logs of containers using docker logs -f <container_id> or docker-compose logs -f in kernelci/kernelci-pipeline or kernelci/kernelci-api directories"
echo "Also you can do docker ps to see running containers, and in case of ongoing builds, you can view their logs too by docker logs -f <container_id>"
echo "You can also open API viewer at http://127.0.0.1:8001/viewer"
