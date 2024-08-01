#!/usr/bin/env python3
'''
KernelCI github tool to create staging snapshot
- Retrieve PRs for a project
- Apply patches for each PR (if user is allowed)
- Create a branch staging-snapshot
- Push branch to origin (if --push is set)
'''

import argparse
import os
import sys
import requests
import json
import logging

'''
Retrieve current PR list for a project
'''

ORG="kernelci"

# AFAIK logging lib doesnt add colors? :(
def print_level(level, text):
    colorcodes = {
        'info': '92',
        'warning': '93',
        'error': '91',
        'red': '91',
        'green': '92',
        'yellow': '93',
        'blue': '94',
        'purple': '95',
    }
    print(f"\033[{colorcodes[level]}m{text}\033[0m")

def get_prs(project):
    url = f"https://api.github.com/repos/{ORG}/{project}/pulls"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()

def shallow_clone(project):
    if not os.path.exists(project):
        url = f"https://github.com/{ORG}/{project}"
        os.system(f"git clone --depth 1 {url}")
    os.chdir(project)
    os.system("git checkout origin/main")
    os.system("git reset --hard origin/main")
    os.system("git am --abort")
    os.chdir("..")

def fetch_pr(pr_number, project):
    url = f"https://github.com/{ORG}/{project}"
    # use .patch at end of URL to get patch
    resp = requests.get(f"{url}/pull/{pr_number}.patch")
    resp.raise_for_status()
    with open(f"patches/pr_{project}-{pr_number}.patch", "w") as f:
        f.write(resp.text)

def apply_pr(pr_number, pr_info, project):
    r = os.system(f"git am -q ../patches/pr_{project}-{pr_number}.patch")
    if r != 0:
        print_level('error', f"{pr_info}: Patch failed for PR {pr_number}")
        os.system("git am --abort")
    else:
        print_level('info', f"{pr_info}: Patch applied for PR {pr_number}")


def load_users(filename):
    if not os.path.exists(filename):
        print_level('error', f"File {filename} not found")
        sys.exit(1)
    with open(filename, "r") as f:
        return f.read().splitlines()

def print_comprehensive(pr, prefix=""):
    for key, value in pr.items():
        if isinstance(value, dict):
            print_comprehensive(value, prefix=f"{prefix}{key}.")
        else:
            print(f"{prefix}={key}: {value}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', help='project', required=True)
    parser.add_argument('--allowusers', help='allowusers', default='users.txt')
    parser.add_argument('--push', help='push to origin', action='store_true', default=False)
    args = parser.parse_args()
    users = load_users(args.allowusers)
    if not os.path.exists("patches"):
        os.mkdir("patches")
    shallow_clone(args.project)
    if args.push:
        os.system(f"git remote set-url --push origin git@github.com:{ORG}/{args.project}.git")
    prs = get_prs(args.project)
    
    # sort ascending by PR number
    prs = sorted(prs, key=lambda pr: pr['number'])
    for pr in prs:
        # for debugging
        #print_comprehensive(pr)
        pr_info = f"PR {pr['number']} - {pr['state']} - {pr['user']['login']} - " + \
                  f"{pr['title']}"

        if pr['user']['login'] not in users:
            print_level('warning', f"{pr_info}: Skipping PR from unauthorized user")
            continue
        if pr['draft']:
            print_level('warning', f"{pr_info}: Skipping PR due to draft status")
            continue
        if 'staging-skip' in [label['name'] for label in pr['labels']]:
            print_level('warning', f"{pr_info}: Skipping PR due to staging-skip label")
            continue
        print_level('info', pr_info)
        fetch_pr(pr['number'], args.project)
        os.chdir(args.project)
        apply_pr(pr['number'], pr_info, args.project)
        os.chdir("..")
    
    # create branch staging-snapshot
    os.chdir(args.project)
    # delete branch if exists
    os.system("git branch -D staging-snapshot")
    os.system("git checkout -b staging-snapshot")
    if args.push:
        os.system("git push -f origin staging-snapshot")
    # retrieve last commit id
    last_commit = os.popen("git log -1 --pretty=%H").read().strip()
    print_level('info', f"Last commit: {last_commit}")
    # write as a file projectname.commit
    os.chdir("..")
    with open(f"{args.project}.commit", "w") as f:
        f.write(last_commit)


if __name__ == '__main__':
    main()

