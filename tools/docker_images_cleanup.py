#!/usr/bin/env python3
import docker
# docker registry
import os
import requests
import json
import time
import argparse

DOCKER_HUB_API = "https://hub.docker.com/v2/"
JWT_TOKEN = ""
AUTHTYPE = "JWT" # "Basic"

def read_auth():
    #with open(os.path.expanduser("~/.docker/config.json"), "r") as f:
    #    data = json.load(f)
    #    return data["auths"]["https://index.docker.io/v1/"]["auth"]

    # file .dockerhub, first line user, second password
    with open(os.path.expanduser(".dockerhub"), "r") as f:
        user = f.readline().strip()
        password = f.readline().strip()
    # get JWT token
    url = f"{DOCKER_HUB_API}users/login/"
    r = requests.post(url, json={"username": user, "password": password})
    if r.status_code != 200:
        print(r.text)
        r.raise_for_status()
    return r.json()["token"]


def print_results(results):
    #print(json.dumps(results, indent=2))
    # iterate over the results and print the image name, last updated and the pull count
    for result in results["results"]:
        print(f"{result['name']},{result['last_updated']},{result['pull_count']}")


def append_results(images, r):
    for result in r["results"]:
        images[result["name"]] = {
            "last_updated": result["last_updated"],
            "pull_count": result["pull_count"],
        }
    return images


'''
List of all images in my hub account
'''
def list_images(org, next=None):
    auth = read_auth()
    headers = {
        "Authorization": f"{AUTHTYPE} {auth}",
    }
    url = f"{DOCKER_HUB_API}repositories/{org}/"
    if next:
        url = next
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(r.text)
        r.raise_for_status()
    return r.json()

def list_all_images(org):
    images = {}
    r = list_images(org)
    images = append_results(images, r)
    while r["next"]:
        r = list_images("kernelci", r["next"])
        images = append_results(images, r)

    return images

def list_tags(org, repo, next=None):
    auth = read_auth()
    headers = {
        "Authorization": f"Basic {auth}",
    }
    url = f"{DOCKER_HUB_API}repositories/{org}/{repo}/tags"
    if next:
        url = next
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(r.text)
        r.raise_for_status()
    return r.json()

def list_all_tags(org, repo):
    tags = {}
    r = list_tags(org, repo)
    for result in r["results"]:
        tags[result["name"]] = {
            "last_updated": result["last_updated"],
            "full_size": result["full_size"],
        }
    while r["next"]:
        r = list_tags(org, repo, r["next"])
        for result in r["results"]:
            tags[result["name"]] = {
                "last_updated": result["last_updated"],
                "full_size": result["full_size"],
            }
    return tags


def delete_image(org, image):
    auth = read_auth()
    headers = {
        "Authorization": f"{AUTHTYPE} {auth}",
    }
    url = f"{DOCKER_HUB_API}repositories/{org}/{image}/"
    r = requests.delete(url, headers=headers)
    if r.status_code != 204:
        print(r.text)
        r.raise_for_status()
    print(f"Deleted {image}")

def delete_tag(org, image, tag):
    auth = read_auth()
    headers = {
        "Authorization": f"{AUTHTYPE} {auth}",
    }
    url = f"{DOCKER_HUB_API}repositories/{org}/{image}/tags/{tag}/"
    r = requests.delete(url, headers=headers)
    if r.status_code != 204:
        print(r.text)
        r.raise_for_status()
    print(f"Deleted {image}")



def main():
    args = argparse.ArgumentParser()
    args.add_argument("--maxage", type=int, default=24, help="Max age of image in month")
    args.add_argument("--clean", action="store_true", help="Clean images")
    args = args.parse_args()
    if not args.clean:
        print("Not cleaning, use --clean to delete old images, --help for help")

    images = list_all_images("kernelci")
    for image in images:
        print(f"Image: {image}")
        print(f"  Last updated: {images[image]['last_updated']}")
        tags = list_all_tags("kernelci", image)
        for tag in tags:
            print(f"  Tag: {tag}")
            print(f"    Last updated: {tags[tag]['last_updated']}")
            print(f"    Full size: {tags[tag]['full_size']}")
            if not args.clean:
                continue
            img_date = time.strptime(tags[tag]['last_updated'], "%Y-%m-%dT%H:%M:%S.%fZ")
            img_unix_ts = time.mktime(img_date)
            now = time.time()
            # month is "approximative"
            if (now - img_unix_ts) > (args.maxage * 30 * 24 * 60 * 60):
                print(f"    Image/tag is too old, delete it")
                # delete image
                delete_tag("kernelci", image, tag)
            


if __name__ == "__main__":
    main()
