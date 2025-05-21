#!/bin/bash -e
# 
# This script will do production deployment of kernelci-api and kernelci-pipeline
# SPDX-License-Identifier: LGPL-2.1-only
# (c) Collabora Ltd. 2023
# Author: Denys Fedoryshchenko <denys.f@collabora.com>

CONTEXT="kernelci-api-1"


install_deps() {
    # install yq
    if ! command -v yq &> /dev/null; then
        echo "yq is missing, installing"
        wget https://github.com/mikefarah/yq/releases/download/v4.45.1/yq_freebsd_amd64 -O yq
        chmod +x yq
    fi
}

fetch_sha256_docker_image() {
    local image=$1
    local tag=$2
    local sha256=$(docker pull $image:$tag | grep -i sha256 | awk '{print $2}')
    echo $sha256
}

fetch_age_docker_image() {
    # use docker inspect to get the age of the image
    local image=$1
    local tag=$2
    local created=$(docker inspect $image:$tag | jq '.[0].Created')
    echo $created
}

clone_kernelci_core() {
    if [ -d kernelci-core ]; then
        echo "Removing existing kernelci-core directory"
        rm -rf kernelci-core
    fi
    git clone https://github.com/kernelci/kernelci-core.git
}

clone_kernelci_api() {
    if [ -d kernelci-api ]; then
        echo "Removing existing kernelci-api directory"
        rm -rf kernelci-api
    fi
    git clone https://github.com/kernelci/kernelci-api.git
}

clone_kernelci_pipeline() {
    if [ -d kernelci-pipeline ]; then
        echo "Removing existing kernelci-pipeline directory"
        rm -rf kernelci-pipeline
    fi
    git clone https://github.com/kernelci/kernelci-pipeline.git
}

clone_repos() {
    clone_kernelci_core
    clone_kernelci_api
    clone_kernelci_pipeline
    cp configmap.yaml kernelci-api/kube/aks/
}

build_kci2_docker_images() {
    # This will invalidate the cache
    cd kernelci-core
    core_rev=$(git show --pretty=format:%H -s origin/main)
    cd ../kernelci-api
    api_rev=$(git show --pretty=format:%H -s origin/main)
    cd ../kernelci-pipeline
    pipeline_rev=$(git show --pretty=format:%H -s origin/main)
    cd ..
    rev_arg="--build-arg core_rev=$core_rev --build-arg api_rev=$api_rev --build-arg pipeline_rev=$pipeline_rev"
    echo "Building kernelci-core docker images with rev: $core_rev $api_rev $pipeline_rev"
    echo "#!/bin/bash" > kernelci-core/dockerbuilds.sh
    echo "python3 -m pip install '.[dev]'" >> kernelci-core/dockerbuilds.sh
    echo "./kci docker build ${rev_arg} --prefix=kernelci/ -v --push kernelci" >> kernelci-core/dockerbuilds.sh
    echo "./kci docker build ${rev_arg} --prefix=kernelci/ -v --push kernelci api" >> kernelci-core/dockerbuilds.sh
    echo "./kci docker build ${rev_arg} --prefix=kernelci/ -v --push kernelci pipeline" >> kernelci-core/dockerbuilds.sh
    echo "./kci docker build ${rev_arg} --prefix=kernelci/ -v --push kernelci lava-callback" >> kernelci-core/dockerbuilds.sh
    chmod +x kernelci-core/dockerbuilds.sh
    # map also host docker and docker credentials
    docker run -v $PWD/kernelci-core:/kernelci-core -w /kernelci-core --rm --privileged \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v $HOME/.docker:/root/.docker \
        python:3.11 ./dockerbuilds.sh
}

update_manifests() {
    # only first document
    UPD="(select(.kind == \"Deployment\" and .metadata.name == \"api\") | .spec.template.spec.containers[0].image) = \"kernelci/kernelci:api@$SHA256_KERNELCI_API\""
    ./yq e "$UPD" -i kernelci-api/kube/aks/api.yaml

    echo "Sleeping 10 seconds before applying kernelci-api"
    sleep 10

    # only first document
    UPD="(select(.kind == \"Deployment\" and .metadata.name == \"lava-callback\") | .spec.template.spec.containers[0].image) = \"kernelci/kernelci:lava-callback@$SHA256_KERNELCI_LAVA\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/lava-callback.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/monitor.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/nodehandlers.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/scheduler-k8s.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/scheduler-lava.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/scheduler-shell.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e -i "$UPD" kernelci-pipeline/kube/aks/tarball.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/trigger.yaml

    UPD=".spec.template.spec.containers[0].image = \"kernelci/kernelci:pipeline@$SHA256_KERNELCI_PIPELINE\""
    ./yq e "$UPD" -i kernelci-pipeline/kube/aks/pipeline-kcidb.yaml

}

apply_manifests() {
    kubectl --context=$CONTEXT apply --namespace kernelci-api -f kernelci-api/kube/aks/api.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/lava-callback.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/monitor.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/nodehandlers.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/scheduler-k8s.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/scheduler-lava.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/scheduler-shell.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/tarball.yaml
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/pipeline-kcidb.yaml

    # BUG/FIXME: trigger need some delay, otherwise if other components are not ready, it will waste the job
    echo "Sleeping 10 seconds before applying trigger"
    sleep 10
    kubectl --context=$CONTEXT apply --namespace kernelci-pipeline -f kernelci-pipeline/kube/aks/trigger.yaml
}

update_configs() {
    CURRENT_DIR=$(pwd)
    TMPDIR=$(mktemp -d)
    echo "Using temporary directory: $TMPDIR"
    cd $TMPDIR
    git clone https://github.com/kernelci/kernelci-pipeline.git
    ls -la kernelci-pipeline
    cd $CURRENT_DIR
    ./api-pipeline-deploy.sh config $TMPDIR/kernelci-pipeline/config
    rm -rf $TMPDIR
}

verify_deps() {
    # configmap.yaml should exist
    if [ ! -f configmap.yaml ]; then
        echo "configmap.yaml is missing"
        exit 1
    fi
}

verify_deps
install_deps

# if we dont have argument "workflow"
# then we will clone all repositories, build docker images, update manifests and apply them
if [ "$1" != "workflow" ]; then
    echo "Cloning all repositories"
    clone_repos

    echo "Building kernelci-core docker images"
    build_kci2_docker_images
fi

echo "Fetching sha256 for kernelci images"
SHA256_KERNELCI_API=$(fetch_sha256_docker_image kernelci/kernelci api)
SHA256_KERNELCI_PIPELINE=$(fetch_sha256_docker_image kernelci/kernelci pipeline)
SHA256_KERNELCI_LAVA=$(fetch_sha256_docker_image kernelci/kernelci lava-callback)

echo "SHA256 API: $SHA256_KERNELCI_API CREATED: $(fetch_age_docker_image kernelci/kernelci api)"
echo "SHA256 PIPELINE: $SHA256_KERNELCI_PIPELINE CREATED: $(fetch_age_docker_image kernelci/kernelci pipeline)"
echo "SHA256 LAVA: $SHA256_KERNELCI_LAVA CREATED: $(fetch_age_docker_image kernelci/kernelci lava-callback)"

echo "Updating configs"
update_configs

echo "Updating manifests"
update_manifests

echo "Applying manifests"
apply_manifests

