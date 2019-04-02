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
import datetime
import json
import os
import requests
import subprocess
import sys
import urllib

GITHUB_API = "https://api.github.com/"
KERNELCI_CORE_URL = "https://github.com/kernelci/kernelci-core.git"


def shell_cmd(cmd):
    subprocess.check_output(cmd, shell=True)


def checkout_repository(args):
    if not os.path.exists(args.path):
        shell_cmd("git clone {url} {path}".format(
            url=args.repo_url, path=args.path))
    shell_cmd("""\
cd {path}
git reset --hard --merge
git fetch origin master
git checkout FETCH_HEAD
""".format(path=args.path))


def get_pull_requests(args):
    path = '/'.join(['repos', args.project, args.repo, 'pulls'])
    base_url = urllib.parse.urljoin(GITHUB_API, path)
    url_params = urllib.parse.urlencode({'state': 'open'})
    url = '?'.join([base_url, url_params])
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    return data


def pull(args, pr):
    head = pr['head']
    try:
        shell_cmd("""\
cd {path}
git pull --no-ff --no-edit {origin} {branch}
""".format(path=args.path,
           origin=head['repo']['clone_url'],
           branch=head['ref']))
    except subprocess.CalledProcessError:
        print("WARNING: Failed to pull branch")
        shell_cmd("""\
cd {path}
git reset --merge
""".format(path=args.path))
        return False
    return True


def apply_patches(args):
    for patch in args.patches:
        print("Applying patch: {}".format(patch))
        try:
            shell_cmd("""\
cat {patch} | (cd {path} && git am)
""".format(path=args.path, patch=patch))
        except subprocess.CalledProcessError:
            print("WARNING: Failed to apply patch")
            shell_cmd("""\
cd {path}
git am --abort
""".format(path=args.path))
            return False
    return True


def create_tag(args):
    tag = args.tag or "staging-{}".format(
        datetime.date.today().strftime('%Y%m%d'))
    print("Tag: {}".format(tag))
    shell_cmd("""\
cd {path}
git tag -l | grep {tag} && git tag -d {tag}
git tag -a {tag} -m {tag}
""".format(path=args.path, tag=tag))
    return tag


def push_tag_and_branch(args, tag):
    print("Function not implemented: push_tag_and_branch()")


def main(args):
    checkout_repository(args)
    prs = get_pull_requests(args)
    for pr in prs:
        pull(args, pr)
    if not apply_patches(args):
        print("Aborting, all patches must apply.")
        return False
    tag = create_tag(args)
    push_tag_and_branch(args, tag)


if __name__ == '__main__':
    parser = argparse.ArgumentParser("Create staging.kernelci.org branches")
    parser.add_argument("path",
                        help="Path to the kernelci-core checkout")
    parser.add_argument("patches", nargs='*',
                        help="Path to extra patches to apply")
    parser.add_argument("--tag",
                        help="Tag to create, default is to use current date")
    parser.add_argument("--project", default="kernelci",
                        help="Name of the Github project")
    parser.add_argument("--repo", default="kernelci-core",
                        help="Name of the Github repository")
    parser.add_argument("--repo-url", default=KERNELCI_CORE_URL,
                        help="URL of the Github repository")
    parser.add_argument("--branch", default="staging.kernelci.org",
                        help="Name of the branch to force-push to")
    args = parser.parse_args(sys.argv[1:])
    main(args)
