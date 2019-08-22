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

import configparser
import datetime
import github
import glob
import os
import subprocess

# Mainline kernel URL from torvalds
TORVALDS_GIT_URL = \
    "git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"

# Terminal colors...
COLORS = {
    'green': '\033[92m',
    'yellow': '\033[93m',
    'red': '\033[91m',
    'blue': '\033[94m',
    'clear': '\033[0m',
}

# Github API handler
GITHUB = github.Github()


def print_color(color, msg):
    print(''.join([COLORS[color], msg, COLORS['clear']]))


def shell_cmd(cmd):
    return subprocess.check_output(cmd, shell=True)


def default_ssh_key(ssh_key, branch):
    if ssh_key:
        return ssh_key

    ssh_key_name = '_'.join(['id_rsa', branch])
    ssh_key_path = os.path.join('keys', ssh_key_name)
    if os.path.exists(ssh_key_path):
        return ssh_key_path

    return None


def ssh_agent(ssh_key, cmd):
    if ssh_key:
        cmd = "ssh-agent sh -c 'ssh-add {key}; {cmd}'".format(
            key=ssh_key, cmd=cmd)
    shell_cmd(cmd)


def date_tag(path, px="staging-", fmt="%Y%m%d"):
    tag_name = "{}{}".format(px, datetime.date.today().strftime(fmt))
    try:
        n = int(shell_cmd("""
cd {path}
git tag -l | grep -c {tag}
""".format(path=path, tag=tag_name)))
    except subprocess.CalledProcessError:
        n = 0
    tag_name = '.'.join([tag_name, str(n)])
    return tag_name


def create_tag(path, tag=None):
    tag = tag or date_tag(path)
    shell_cmd("""\
cd {path}
git tag -l | grep {tag} && git tag -d {tag}
git tag -a {tag} -m {tag}
""".format(path=path, tag=tag))
    return tag


def checkout_repository(path, repo, origin="origin", branch="master"):
    if not os.path.exists(path):
        shell_cmd("""\
git clone {url} {path}
cd {path}
git remote set-url --push origin {push}
""".format(path=path, url=repo.clone_url, push=repo.ssh_url))

    shell_cmd("""\
cd {path}
git reset --quiet --hard --merge
git fetch --quiet {origin} {branch}
git checkout FETCH_HEAD
git config user.name "kernelci.org bot"
git config user.email "bot@kernelci.org"
""".format(path=path, origin=origin, branch=branch))


def apply_patches(path, patches_path):
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


def push_tag_and_branch(path, ssh_key, branch, tag):
    ssh_agent(ssh_key, """\
cd {path}
git push --quiet --force origin HEAD:{branch} {tag}
""".format(path=path, branch=branch, tag=tag))


class Settings:

    def __init__(self, path, section):
        self._settings = configparser.ConfigParser()
        if path:
            self._settings.read(path)
        self._section = section

    def get(self, option, as_list=False):
        if not self._settings.has_option(self._section, option):
            return None
        value = self._settings.get(self._section, option).split()
        if not as_list and len(value) == 1:
            value = value[0]
        return value
