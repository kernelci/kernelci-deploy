#!/usr/bin/env python3
#
# Copyright (C) 2021 Collabora Limited
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
from kernelci import print_color, shell_cmd, ssh_agent


def do_rebase(path):
    shell_cmd("""\
cd {path}
git pull --rebase origin main
""".format(path=path))


def do_push(path, ssh_key, tag, branch):
    diff = kernelci.origin_diff(path, branch)
    if not diff:
        print_color('yellow', "No changes, not pushing.")
        return True
    print(diff)

    kernelci.create_tag(path, tag)

    print("\nPushing tag ({}) and branch ({})".format(tag, branch))
    kernelci.push_tag_and_branch(path, ssh_key, branch, tag)


def main(args):
    repo_name = '/'.join([args.namespace, args.project])
    repo = kernelci.GITHUB.get_repo(repo_name)
    path = os.path.join('checkout', args.project)
    kernelci.checkout_repository(path, repo, branch=args.branch)

    do_rebase(path)

    if args.push:
        ssh_key = kernelci.default_ssh_key(args.ssh_key, args.branch)
        if not ssh_key:
            print_color('red', "No SSH key provided, cannot push.")
            return False

        tag = args.tag or kernelci.date_tag(path, args.tag_prefix)
        do_push(path, ssh_key, tag, args.branch)

    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser("\
Update kernelci.org branch with latest changes")
    parser.add_argument("project",
                        help="Name of the Github project")
    parser.add_argument("--tag",
                        help="Tag to create, default is to use current date")
    parser.add_argument("--tag-prefix", default="kernelci-",
                        help="Prefix to create date with current date")
    parser.add_argument("--branch", default='kernelci.org',
                        help="Name of the branch to force-push to")
    parser.add_argument("--namespace", default='kernelci',
                        help="Github project namespace, default is kernelci")
    parser.add_argument("--ssh-key",
                        help="Path to SSH key to push branches and tags")
    parser.add_argument("--push", action="store_true",
                        help="Push the resulting branch and tag")
    args = parser.parse_args(sys.argv[1:])
    ret = main(args)
    sys.exit(0 if ret is True else 1)
