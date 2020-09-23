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
    settings = kernelci.Settings(args.settings, args.project)
    path = os.path.join('checkout', args.project)
    namespace = args.namespace or settings.get('namespace') or 'kernelci'
    repo_name = '/'.join([namespace, args.project])
    repo = kernelci.GITHUB.get_repo(repo_name)
    kernelci.checkout_repository(path, repo)
    prs = repo.get_pulls()
    raw_skip = args.skip or settings.get('skip', as_list=True) or []
    skip = []
    for user_branch in raw_skip:
        user, _, branch = user_branch.partition('/')
        skip.append((user, branch))
    print("\n{:4} {:16} {:32} {}".format("PR", "User", "Branch", "Status"))
    print("-------------------------------------------------------------")
    users = settings.get('users', as_list=True)
    if not users:
        print_color('red', "Aborting, no list of trusted users")
        return False
    for pr in reversed(list(prs)):
        branch = pr.head.ref
        user = pr.head.repo.owner.login
        labels = list(label.name for label in pr.labels)
        print("{:4} {:16} {:32} ".format(pr.number, user, branch), end='')
        if user not in users:
            print_color('red', "SKIP untrusted user")
        elif (user, branch) in skip:
            print_color('yellow', "SKIP")
        elif pr.base.ref != args.master:
            print_color('yellow', "SKIP base: {}".format(pr.base.ref))
        elif args.skip_label and args.skip_label in labels:
            print_color('yellow', "SKIP label: {}".format(args.skip_label))
        else:
            if pull(args, pr, path):
                print_color('green', "OK")
    print()
    patches_path = os.path.join('patches', args.project)
    if not kernelci.apply_patches(path, patches_path):
        print_color('red', "Aborting, all patches must apply.")
        return False
    tag = None if args.no_tag else (
        args.tag or kernelci.date_tag(path, args.tag_prefix)
    )
    if tag:
        kernelci.create_tag(path, tag)
    if args.push:
        branch = args.branch or settings.get('branch')
        if not branch:
            print_color('red', "No destination branch provided.")
            return False
        print("\nPushing tag ({}) and branch ({})".format(tag, branch))
        ssh_key = kernelci.default_ssh_key(args.ssh_key, branch)
        if not ssh_key:
            print_color('red', "No SSH key provided.")
            return False
        kernelci.push_tag_and_branch(path, ssh_key, branch, tag)
    elif tag:
        print("\nTag: {}".format(tag))
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser("\
Create staging.kernelci.org branch with all pending PRs")
    parser.add_argument("project",
                        help="Name of the Github project")
    parser.add_argument("--tag",
                        help="Tag to create, default is to use current date")
    parser.add_argument("--tag-prefix", default="staging-",
                        help="Prefix to create date with current date")
    parser.add_argument("--no-tag", action='store_true',
                        help="Don't create any tag")
    parser.add_argument("--branch",
                        help="Name of the branch to force-push to")
    parser.add_argument("--master", default="master",
                        help="Name of the master branch to filter PRs")
    parser.add_argument("--namespace",
                        help="Github project namespace, default is kernelci")
    parser.add_argument("--skip", nargs='+', default=[],
                        help="Name of user/branch pairs to skip")
    parser.add_argument("--skip-label", default="staging-skip",
                        help="Name of a Github label used to skip PRs")
    parser.add_argument("--ssh-key",
                        help="Path to SSH key to push branches and tags")
    parser.add_argument("--push", action="store_true",
                        help="Push the resulting branch and tag")
    parser.add_argument("--settings", default="data/staging.ini",
                        help="Path to a settings file")
    args = parser.parse_args(sys.argv[1:])
    ret = main(args)
    sys.exit(0 if ret is True else 1)
