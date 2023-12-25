#!/usr/bin/env python3
"""
(c) 2023 Collabora Ltd.
Author: Denys Fedoryshchenko <denys.f@collabora.com>
SPDX-License-Identifier: LGPL-2.1-or-later

Script to build rootfs images for KernelCI
"""

import argparse
import os
import docker
import time
import yaml
import sys
import signal
import hashlib

VERSION="0.2"
containerid=""


def signal_handler(sig, frame):
    if containerid == "":
        print("Container not found, exiting")
        sys.exit(0)
    print('Please wait, deleting container, otherwise you need to do it manually to avoid "zombie" containers')
    client = docker.from_env()
    client.containers.get(containerid).stop()
    client.containers.get(containerid).remove(force=True)
    print("Done")
    sys.exit(0)

def get_containerid():
    """
    Generate short UUID as hash of current directory
    """
    # get current directory
    cwd = os.getcwd()
    print("Current directory: "+cwd)
    # get hash of current directory, truncate to 8 symbols
    UUID = hashlib.sha256(cwd.encode()).hexdigest()[:8]
    containerid = "kernelci-build-"+UUID
    return containerid

def clean_cache():
    print("Cleaning cache... please wait")
    if os.path.isdir("kernelci-core/temp/temp"):
        os.system("rm -rf kernelci-core/temp/temp")
    print("Cache cleaned")


def verify_docker():    
    """
    Verify that docker is installed, if require sudo, and if running
    """
    # docker.ping returns True if docker is running
    try:
        if not docker.from_env().ping():
            print("Docker is not running")
            exit(1)
    except Exception as e:
        print("Docker is not installed or requires sudo")
        exit(1)


def prepare_kci_source(branch):
    if not os.path.isdir("kernelci-core"):
        r = os.system(f"git clone --branch {branch} https://github.com/kernelci/kernelci-core/")
        if r != 0:
            print("Failed to clone kernelci-core")
            exit(1)
    else:
        print("kernelci-core directory already present, skipping git clone")

    # create kernelci-core/temp
    if not os.path.isdir("kernelci-core/temp"):
        os.mkdir("kernelci-core/temp")
    
    # chown kernelci-core/temp to 1000:1000
    os.system("chown -R 1000:1000 kernelci-core/temp")



# TODO/FIX: This is not working well, might break even running build
def prepare_docker_container(imagename, container_name):
    # workaround, debos need to create fakemachine scratch volume file
    # under uid 1000, so we need to set kernelci-core writable by uid 1000
    # TODO(nuclearcat): chdir to temp dir?
    os.system("chown -R 1000:1000 kernelci-core")
    # is kernelci-build-cross container present and running?
    # if yes, stop and remove it to refresh
    client = docker.from_env()
    # is kernelci-build-cross container already present?
    try:
        client.containers.get(container_name).remove(force=True)
        # Wait for container to be removed
        while True:
            try:
                client.containers.get(container_name)
                print(container_name+" container still present, waiting...")
            except docker.errors.NotFound:
                print(container_name+" container removed")
                break
    except docker.errors.NotFound:
        pass

    # pull latest version (it might use stale cached image)
    print("Pulling "+imagename+" image")
    try:
        client.images.pull(imagename)
    except docker.errors.ImageNotFound:
        print("Image "+imagename+" not found")
        exit(1)
    print("Launching "+container_name+" container from "+imagename+" image")
    try:
        # /dev/shm is a tmpfs: rw,nosuid,nodev,exec
        client.containers.run(
            imagename,
            name=container_name,
            detach=True,
            init=True,
            tty=True,
            user="0:0",
            volumes={
                os.getcwd()+ "/kernelci-core": {
                    "bind": "/kernelci-core",
                    "mode": "rw"
                },
                os.getcwd() + "/kernelci-core/config": {
                    "bind": "/etc/kernelci",
                    "mode": "rw"
                },
                "/dev": {
                    "bind": "/dev",
                    "mode": "rw"
                },
            },
            tmpfs={
                "/dev/shm": "rw,nosuid,nodev,exec"
            },
            devices=[
                "/dev/kvm"
            ],
            privileged=True,
            command="bash"
        )
    except docker.errors.APIError as e:
        print("Failed to launch container: "+str(e))
        exit(1)

    # Wait for container to be ready and running state
    while True:
        try:
            if client.containers.get(container_name):
                print("Container "+container_name+" is ready")

                break
        except docker.errors.APIError:
            print("Waiting for container to be ready...")
            time.sleep(1)
            pass

    return container_name


def rootfs_config(rootfs_name):
    # read config file kernelci-core/config/core/rootfs-configs.yaml
    with open("kernelci-core/config/core/rootfs-configs.yaml", "r") as f:
        y = yaml.load(f, Loader=yaml.FullLoader)
    
    # find rootfs_name in rootfs_configs
    for rootfs in y["rootfs_configs"]:
        if rootfs == rootfs_name:
            return y["rootfs_configs"][rootfs_name]


def build_image(name, arch, rootfs_type, container_name):
    client = docker.from_env()

    print(f"Building kernelci rootfs {name} for {arch} type {rootfs_type}")

    # pipeline is yyyymmdd.n
    pipeline = time.strftime("%Y%m%d")+".0"
    cmd = f"bash -c \"cd /kernelci-core; ./kci_rootfs build --rootfs-config {name} --arch {arch} --data-path /etc/kernelci/rootfs/{rootfs_type} --output /kernelci-core/temp/{pipeline}\"";
    print("Launching container: "+cmd)
    container = client.containers.get(container_name).exec_run(cmd, stream=True)

    # monitor r.output stream
    tstamp = os.popen("date +%Y%m%d%H%M%S").read().strip()
    fname = "log-"+name+"-"+arch+"-"+tstamp+".log"
    with open(fname, "w") as f:
        for line in container.output:
            # Sometimes we get UnicodeDecodeError :-\
            try:
                decoded = line.decode("utf-8")
            except UnicodeDecodeError:
                decoded = "UnicodeDecodeError"
            f.write(decoded)
            print(decoded, end="")

    print("Image built successfully")    

    # compress log file
    os.system("gzip "+fname)

    # TODO: cleanup


def cleanup_container(container_name):
    print("Stopping container "+container_name)
    client = docker.from_env()
    client.containers.get(container_name).stop()
    print("Removing container "+container_name)
    # Wait until stopped
    while True:
        try:
            if client.containers.get(container_name).status == "exited":
                print("Container "+container_name+" stopped")
                break
        except docker.errors.APIError:
            print("Waiting for container to stop...")
            time.sleep(1)

    client.containers.get(container_name).remove(force=True)

def get_all_rootfs():
    """
    Return list of all rootfs names
    """
    with open("kernelci-core/config/core/rootfs-configs.yaml", "r") as f:
        y = yaml.load(f, Loader=yaml.FullLoader)
    return y["rootfs_configs"]


def main():
    global containerid
    # add signal handler for INT/TERM to not leave dangling containers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description='Build rootfs images for KernelCI v'+VERSION)
    parser.add_argument('--arch', help='Architecture to build for, e.g. arm64,arm,amd64')
    parser.add_argument('--name', help='Name of the rootfs to build')
    parser.add_argument('--branch', help='Branch of kernelci-core to use', default="main")
    parser.add_argument('--all', help='Build all rootfs images', action='store_true')
    args = parser.parse_args()

    containerid = get_containerid()
    verify_docker()
    prepare_kci_source(args.branch)

    if args.all:
        # iterate key,value
        allfs = get_all_rootfs()
        for rootfs in allfs:
            arch_list = allfs[rootfs]["arch_list"]
            rootfs_type = allfs[rootfs]["rootfs_type"]
            prefix = ""
            # TODO: staging- cros-
            docker_image = f"kernelci/{prefix}{rootfs_type}:kernelci"
            prepare_docker_container(docker_image, containerid)
            for arch in arch_list:
                print(f"Building {rootfs} for {arch} type {rootfs_type}")
                build_image(rootfs, arch, rootfs_type, containerid)
            # stop and remove container
            cleanup_container(containerid)
        exit(0)

    # otherwise arch and name are required
    if args.arch is None or args.name is None:
        print("Please specify --arch and --name")
        exit(1)

    fs_config = rootfs_config(args.name)
    try:
        rootfs_type = fs_config["rootfs_type"]
    except KeyError:
        print("rootfs_type not found in config")
        exit(1)

    prefix = ""
    # TODO: staging- cros-
    docker_image = f"kernelci/{prefix}{rootfs_type}:kernelci"
    prepare_docker_container(docker_image, containerid)

    # if args.arch have a comma, build for all archs in the list
    if "," in args.arch:
        archs = args.arch.split(",")
        for arch in archs:
            build_image(args.name, arch, rootfs_type, containerid)
    else:
        build_image(args.name, args.arch, rootfs_type, containerid)

    # stop and remove container
    cleanup_container(containerid)

if __name__ == "__main__":
    main()