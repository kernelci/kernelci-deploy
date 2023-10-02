#!/usr/bin/env python3
#
# Copyright (C) 2023 Collabora Limited
# Author: Denys Fedoryshchenko <denys.f@collabora.com
#
# SPDX-License-Identifier: LGPL-2.1-or-later

"""
Monitor docker containers in current directory docker-compose.yml
Return state over /state endpoint
"""

import docker
import time
import os
import sys
import yaml
import fastapi
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn
import argparse

state = {}

COMPOSE_FILES = ()

app = FastAPI()


@app.get("/state")
async def state():
    c = docker.from_env()
    containers = get_compose_containers()
    states = {}
    for container in containers:
        try:
            states[container] = c.containers.get(container).status
        except docker.errors.NotFound:
            states[container] = "not found"
        except docker.errors.APIError:
            states[container] = "api error"
        except Exception as e:
            states[container] = "unknown error"
            print(e)
    return JSONResponse(content=states)


"""
Get docker containers names in current directory docker-compose.yml
"""
def get_compose_containers():
    containers = []
    for compose_file in COMPOSE_FILES:
        with open(compose_file, "r") as f:
            compose = yaml.safe_load(f.read())
        for service in compose["services"]:
            containers.append(compose["services"][service]["container_name"])
    return containers


def __main__():
    global COMPOSE_FILES
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compose-files",
        nargs="+",
        help="docker-compose files to monitor",
        required=True,
    )
    args = parser.parse_args()
    COMPOSE_FILES = args.compose_files
    print(f"Monitoring docker-compose files: {COMPOSE_FILES}")

    uvicorn.run(app, host="127.0.0.1", port=8008)

if __name__ == "__main__":
    __main__()
