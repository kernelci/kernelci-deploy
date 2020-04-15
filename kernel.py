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
import os
import sys

import kernelci
from kernelci import print_color, shell_cmd


def add_date_commit(path, msg, fname):
    with open(os.path.join(path, fname), 'w') as f:
        f.write(msg)
    shell_cmd("""
cd {path}
git add {fname}
git commit --author='"kernelci.org bot" <bot@kernelci.org>' -am {msg}
""".format(path=path, fname=fname, msg=msg))


def main(args):
    path = os.path.join('checkout', args.project)
    print("path: {}".format(path))
    repo_name = '/'.join([args.namespace, args.project])
    print("repo: {}".format(repo_name))
    repo = kernelci.GITHUB.get_repo(repo_name)
    print("checking out {} {}".format(args.from_url, args.from_branch))
    kernelci.checkout_repository(path, repo, args.from_url, args.from_branch)
    patches_path = os.path.join('patches', args.project, args.branch)
    print("patches: {}".format(patches_path))
    if not kernelci.apply_patches(path, patches_path):
        print_color('red', "Aborting, all patches must apply.")
        return False
    tag = args.tag or kernelci.date_tag(path, args.tag_prefix)
    print("tag: {}".format(tag))
    add_date_commit(path, tag, args.branch)
    kernelci.create_tag(path, tag)
    if args.push:
        print("\nPushing tag ({}) and branch ({})".format(tag, args.branch))
        ssh_key = kernelci.default_ssh_key(args.ssh_key, args.branch)
        if not ssh_key:
            print_color('red', "No SSH key provided.")
            return False
        kernelci.push_tag_and_branch(path, ssh_key, args.branch, tag)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser("\
Create a kernel branch to test KernelCI")
    parser.add_argument("--tag",
                        help="Tag to create, default is to use current date")
    parser.add_argument("--tag-prefix", default="staging-",
                        help="Prefix to create date with current date")
    parser.add_argument("--branch", default="staging.kernelci.org",
                        help="Name of the branch to force-push to")
    parser.add_argument("--from-url", default=kernelci.TORVALDS_GIT_URL,
                        help="URL to pull from")
    parser.add_argument("--from-branch", default="master",
                        help="Branch to pull from")
    parser.add_argument("--namespace", default='kernelci',
                        help="Github project namespace")
    parser.add_argument("--project", default='linux',
                        help="Name of the Github project")
    parser.add_argument("--ssh-key",
                        help="Path to SSH key to push branches and tags")
    parser.add_argument("--push", action="store_true",
                        help="Push the resulting branch and tag")
    args = parser.parse_args(sys.argv[1:])
    ret = main(args)
    sys.exit(0 if ret is True else 1)
