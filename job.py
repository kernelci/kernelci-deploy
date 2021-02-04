#!/usr/bin/env python3
#
# Copyright (C) 2019 Collabora Limited
# Author: Guillaume Tucker <guillaume.tucker@collabora.com>
#
# This module is free software; you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2.1 of the License, or (at your option)
# any later version.
#
# This library is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this library; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import argparse
import jenkins
import json
import sys
import time

import kernelci

ACTIONS = [
    "trigger",
    "enable",
    "disable",
    "flush",
]


def cmd_trigger(args, api):
    if args.json:
        with open(args.json) as f:
            params = json.load(f)
    elif args.no_params:
        params = None
    else:
        params = {'_': ''}  # silly...
    api.build_job(args.job, params)


def cmd_enable(args, api):
    api.enable_job(args.job)


def cmd_disable(args, api):
    api.disable_job(args.job)


def _get_building_jobs(api, job_name, depth=100):
    job_info = api.get_job_info(job_name)
    builds = job_info['builds']
    building = set()

    for build in builds[:depth]:
        build_number = build['number']
        build_info = api.get_build_info(job_name, build['number'])
        build_status = build_info['building']
        if build_status:
            print("building: {} #{}".format(job_name, build_number))
            building.add(build_number)

    return building


def cmd_flush(args, api):
    building = _get_building_jobs(api, args.job)

    while building:
        while building:
            print("still building: {}".format(len(building)))
            print("waiting...")
            time.sleep(15)
            still_building = set()
            for number in building:
                is_building = api.get_build_info(args.job, number)['building']
                if is_building:
                    still_building.add(number)
                building = still_building
        building = _get_building_jobs(api, args.job)

    print("No more {} jobs running.".format(args.job))


def main(args):
    settings = kernelci.Settings(args.settings, args.section)
    url = args.url or settings.get('url')
    user = args.user or settings.get('user')
    token = args.token or settings.get('token')
    if not all((url, user, token)):
        print("Missing info: url, user and token are required")
        return False

    api = jenkins.Jenkins(url, user, token)
    cmd = "_".join(["cmd", args.action])
    globals()[cmd](args, api)

    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser("Control Jenkins jobs")
    parser.add_argument("action", choices=ACTIONS,
                        help="action to perform")
    parser.add_argument("job",
                        help="Name of the Jenkins job")
    parser.add_argument("--url",
                        help="Jenkins API URL")
    parser.add_argument("--user",
                        help="Jenkins user name")
    parser.add_argument("--token",
                        help="Jenkins API token")
    parser.add_argument("--json",
                        help="Path to a JSON file with job parameters")
    parser.add_argument("--no-params", action='store_true',
                        help="Trigger job with no parameters")
    parser.add_argument("--settings", default="data/staging-jenkins.ini",
                        help="Path to a settings file")
    parser.add_argument("--section", default="jenkins",
                        help="Section in the settings file")
    args = parser.parse_args(sys.argv[1:])
    ret = main(args)
    sys.exit(0 if ret is True else 1)
