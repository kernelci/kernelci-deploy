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


def main(args):
    api = jenkins.Jenkins(args.url, args.user, args.token)
    if args.json:
        with open(args.json) as f:
            params = json.load(f)
    api.build_job(args.job, params)


if __name__ == '__main__':
    parser = argparse.ArgumentParser("\
Submit Jenkins jobs")
    parser.add_argument("job",
                        help="Name of the Jenkins job to schedule")
    parser.add_argument("--url",
                        help="Jenkins API URL")
    parser.add_argument("--user",
                        help="Jenkins user name")
    parser.add_argument("--token",
                        help="Jenkins API token")
    parser.add_argument("--json",
                        help="Path to a JSON file with job parameters")
    args = parser.parse_args(sys.argv[1:])
    ret = main(args)
    sys.exit(0 if ret is True else 1)
