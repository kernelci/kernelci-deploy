#!/bin/bash
. ./main.cfg

# i am groot?
if [ $(id -u) -ne 0 ]; then
    SUDO=sudo
else
    SUDO=
fi

function failonerror {
    if [ $? -ne 0 ]; then
        echo "Failed"
        exit 1
    fi
}

# if directry kernelci doesn't exist, then we dont have repos cloned
if [ ! -d kernelci ]; then
    echo Create kernelci directory, clone repos and checkout branches
    mkdir kernelci
    cd kernelci
    echo Clone core, api and pipeline repos

    echo Clone core repo
    git clone $KCI_CORE_REPO
    cd kernelci-core
    git fetch origin
    git checkout origin/$KCI_CORE_BRANCH
    cd ..

    echo Clone api repo
    git clone $KCI_API_REPO
    cd kernelci-api
    git fetch origin
    git checkout origin/$KCI_API_BRANCH
    cd ..

    echo Clone pipeline repo
    git clone $KCI_PIPELINE_REPO
    cd kernelci-pipeline
    git fetch origin
    git checkout origin/$KCI_PIPELINE_BRANCH
    cd ..
else
    cd kernelci
fi

# if KCI_CACHE set, git clone linux kernel tree and keep as archive
if [ -n "$KCI_CACHE" ]; then
    if [ ! -f ../linux.tar ]; then
        git clone https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git linux
        tar -cf ../linux.tar linux
        rm -rf linux
    fi
fi

# if KCI_CACHE set, unpack linux kernel tree to 
# kernelci/kernelci-pipeline/data/src
if [ -n "$KCI_CACHE" ]; then
    if [ ! -d kernelci-pipeline/data/src/linux ]; then
        tar -xf ../linux.tar -C kernelci-pipeline/data/src
        $SUDO chown -R 1000:1000 kernelci-pipeline/data/src/linux
    fi
fi

# build docker images
# purge docker build cache with confirmation
echo "Purge docker build cache"
docker builder prune -f

cd kernelci-api
echo Retrieve API revision and branch
api_rev=$(git show --pretty=format:%H -s origin/$KCI_API_BRANCH)
api_url=$(git remote get-url origin)
cd ..
cd kernelci-core
echo Retrieve Core revision and branch
core_rev=$(git show --pretty=format:%H -s origin/$KCI_CORE_BRANCH)
core_url=$(git remote get-url origin)
build_args="--build-arg core_rev=$core_rev --build-arg api_rev=$api_rev --build-arg core_url=$core_url --build-arg api_url=$api_url"
px_arg='--prefix=local/staging-'
args="build --verbose $px_arg $build_args"
echo Build docker images: kernelci args=$args
./kci docker $args kernelci 
failonerror
echo Build docker images: k8s+kernelci
./kci docker $args k8s kernelci
failonerror
echo Build docker images: api
./kci docker $args api --version="$api_rev"
failonerror
echo Tag docker image of api to latest
docker tag local/staging-api:$api_rev local/staging-api:latest
failonerror
echo Build docker images: clang-17+kselftest+kernelci for x86
./kci docker $args clang-17 kselftest kernelci --arch x86
failonerror
echo Build docker images: gcc-10+kselftest+kernelci for x86
./kci docker $args gcc-10 kselftest kernelci --arch x86
failonerror
echo Build docker images: gcc-10+kselftest+kernelci for arm64
./kci docker $args gcc-10 kselftest kernelci --arch arm64
failonerror


