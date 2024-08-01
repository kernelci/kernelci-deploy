#!/bin/bash
# $(git show --pretty=format:%H -s origin/staging.kernelci.org)
REPOS=("kernelci-core" "kernelci-pipeline" "kernelci-api")

preparing_repos() {
    echo "Preparing repos staging snapshots branch"
    for repo in ${REPOS[@]}; do
        ./staging-branch.py --project $repo --push
    done
}

retrieving_revs() {
    echo "Retrieving revisions"
    core_rev=$(cat kernelci-core.commit)
    pipeline_rev=$(cat kernelci-pipeline.commit)
    api_rev=$(cat kernelci-api.commit)

    echo "kernelci-core: $core_rev"
    echo "kernelci-pipeline: $pipeline_rev"
    echo "kernelci-api: $api_rev"
}

building_docker() {
    echo "Building docker images"
    retrieving_revs
    if [ -z "$core_rev" ] || [ -z "$pipeline_rev" ] || [ -z "$api_rev" ]; then
        echo "Error: One or more rev variables are not set"
        exit 1
    fi
    cache_arg=""
    rev_arg="--build-arg core_rev=$core_rev --build-arg api_rev=$api_rev --build-arg pipeline_rev=$pipeline_rev"
    px_arg='--prefix=kernelci/staging-'
    #args="build --push $px_arg $cache_arg $rev_arg"
    args="build $px_arg $cache_arg $rev_arg"
    cd kernelci-core
    # install dependencies
    pip install -r requirements.txt --break-system-packages
    ./kci docker $args kernelci
}

preparing_repos
building_docker


