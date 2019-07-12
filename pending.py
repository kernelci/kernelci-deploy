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
import json
import os
import subprocess
import sys

import kernelci
from kernelci import print_color, shell_cmd, ssh_agent

# List of trusted users
USERS = [
    'broonie',
    'chaws',
    'danrue',
    'eballetbo',
    'gctucker',
    'kernelci',
    'khilman',
    'mattface',
    'mgalka',
    'montjoie',
    'roxell',
    'touilkhouloud',
]


def pull(args, pr, path):
    branch = pr.head.ref
    user = pr.head.repo.owner.login
    try:
        shell_cmd("""\
cd {path}
git pull --quiet --no-ff --no-edit {origin} {branch}
""".format(path=path, origin=pr.head.repo.clone_url, branch=branch))
    except subprocess.CalledProcessError:
        print_color('yellow', "FAILED to pull")
        shell_cmd("""\
cd {path}
git reset --merge
""".format(path=path))
        return False
    return True


def main(args):
    path = os.path.join('checkout', args.project)
    repo_name = '/'.join([args.namespace, args.project])
    repo = kernelci.GITHUB.get_repo(repo_name)
    kernelci.checkout_repository(path, repo)
    prs = repo.get_pulls()
    skip = []
    for user_branch in args.skip:
        user, _, branch = user_branch.partition('/')
        skip.append((user, branch))
    print("\n{:4} {:16} {:32} {}".format("PR", "User", "Branch", "Status"))
    print("-------------------------------------------------------------")
    for pr in reversed(list(prs)):
        branch = pr.head.ref
        user = pr.head.repo.owner.login
        print("{:4} {:16} {:32} ".format(pr.number, user, branch), end='')
        if user not in USERS:
            print_color('red', "SKIP untrusted user")
        elif (user, branch) in skip:
            print_color('yellow', "SKIP")
        else:
            if pull(args, pr, path):
                print_color('green', "OK")
    print()
    patches_path = os.path.join('patches', args.project)
    if not kernelci.apply_patches(path, patches_path):
        print_color('red', "Aborting, all patches must apply.")
        return False
    tag = kernelci.create_tag(path, args.tag)
    if args.push:
        print("\nPushing tag ({}) and branch ({})".format(tag, args.branch))
        ssh_key = kernelci.default_ssh_key(args.ssh_key, args.branch)
        if not ssh_key:
            print_color('red', "No SSH key provided.")
            return False
        kernelci.push_tag_and_branch(path, ssh_key, args.branch, tag)
    else:
        print("\nTag: {}".format(tag))


if __name__ == '__main__':
    parser = argparse.ArgumentParser("\
Create staging.kernelci.org branch with all pending PRs")
    parser.add_argument("project",
                        help="Name of the Github project")
    parser.add_argument("--tag",
                        help="Tag to create, default is to use current date")
    parser.add_argument("--branch", default="staging.kernelci.org",
                        help="Name of the branch to force-push to")
    parser.add_argument("--namespace", default='kernelci',
                        help="Github project namespace")
    parser.add_argument("--skip", nargs='+', default=[],
                        help="Name of user/branch pairs to skip")
    parser.add_argument("--ssh-key",
                        help="Path to SSH key to push branches and tags")
    parser.add_argument("--push", action="store_true",
                        help="Push the resulting branch and tag")
    args = parser.parse_args(sys.argv[1:])
    main(args)
