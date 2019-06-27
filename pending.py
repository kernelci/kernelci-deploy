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
import github
import glob
import json
import os
import subprocess
import sys

GITHUB = github.Github()

# List of trusted users
USERS = [
    'broonie',
    'chaws',
    'danrue',
    'gctucker',
    'kernelci',
    'khilman',
    'mattface',
    'mgalka',
    'montjoie',
    'roxell',
    'touilkhouloud',
]

# Terminal colors...
COLORS = {
    'green': '\033[92m',
    'yellow': '\033[93m',
    'red': '\033[91m',
    'blue': '\033[94m',
    'clear': '\033[0m',
}

def print_color(color, msg):
    print(''.join([COLORS[color], msg, COLORS['clear']]))


def shell_cmd(cmd):
    subprocess.check_output(cmd, shell=True)


def ssh_agent(ssh_key, cmd):
    if ssh_key:
        cmd = "ssh-agent sh -c 'ssh-add {key}; {cmd}'".format(
            key=ssh_key, cmd=cmd)
    shell_cmd(cmd)


def checkout_repository(args, path, repo):
    if not os.path.exists(path):
        shell_cmd("""\
git clone {url} {path}
cd {path}
git remote set-url --push origin {push}
""".format(path=path, url=repo.clone_url, push=repo.ssh_url))

    shell_cmd("""\
cd {path}
git reset --quiet --hard --merge
git fetch --quiet origin master
git checkout FETCH_HEAD
""".format(path=path))


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


def apply_patches(args, path, patches_path):
    patches = sorted(glob.glob(os.path.join(patches_path, '*.patch')))
    for patch in patches:
        print("Applying patch: {}".format(patch))
        try:
            shell_cmd("""\
cat {patch} | (cd {path} && git am)
""".format(path=path, patch=patch))
        except subprocess.CalledProcessError:
            print("WARNING: Failed to apply patch")
            shell_cmd("""\
cd {path}
git am --abort
""".format(path=path))
            return False
    return True


def create_tag(args, path):
    tag = args.tag or "staging-{}".format(
        datetime.date.today().strftime('%Y%m%d'))
    shell_cmd("""\
cd {path}
git tag -l | grep {tag} && git tag -d {tag}
git tag -a {tag} -m {tag}
""".format(path=path, tag=tag))
    return tag


def push_tag_and_branch(args, path, tag):
    ssh_agent(args.ssh_key, """\
cd {path}
git push --quiet --force origin HEAD:{branch} {tag}
""".format(path=path, branch=args.branch, tag=tag))


def main(args):
    path = os.path.join('checkout', args.project)
    repo_name = '/'.join([args.namespace, args.project])
    repo = GITHUB.get_repo(repo_name)
    checkout_repository(args, path, repo)
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
    if not apply_patches(args, path, patches_path):
        print_color('red', "Aborting, all patches must apply.")
        return False
    tag = create_tag(args, path)
    if args.push:
        print("\nPushing tag ({}) and branch ({})".format(tag, args.branch))
        push_tag_and_branch(args, path, tag)
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
