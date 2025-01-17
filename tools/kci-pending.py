#!/usr/bin/env python3
'''
Poll pending PRs for project, verify if they match criteria and merge them
'''

import os
import sys
import json
import requests
import argparse
import time
import random
import logging
import tempfile
import configparser

PROJECT = 'kernelci'
patch_files = []

# Set up logging, add colors
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.addLevelName(logging.INFO, "\033[1;32m%s\033[1;0m" % logging.getLevelName(logging.INFO))
logging.addLevelName(logging.ERROR, "\033[1;31m%s\033[1;0m" % logging.getLevelName(logging.ERROR))
logging.addLevelName(logging.WARNING, "\033[1;33m%s\033[1;0m" % logging.getLevelName(logging.WARNING))
logging.addLevelName(logging.DEBUG, "\033[1;34m%s\033[1;0m" % logging.getLevelName(logging.DEBUG))


def clone_repo(owner, args):
    url = f'https://github.com/{owner}/{args.repo}.git'
    if os.path.exists(args.repo):
        os.system(f'rm -rf {args.repo}')
    os.system(f'git clone --depth 1 {url}')
    # if args.push then add push url
    if args.push:
        os.chdir(args.repo)
        os.system(f'git remote set-url origin https://{args.push}@github.com/{owner}/{args.repo}')
        os.chdir('..')


def get_prs(owner, repo):
    url = f'https://api.github.com/repos/{owner}/{repo}/pulls'
    headers = {
        'Accept': 'application/vnd.github.v3+json'
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        logging.error(f'Failed to fetch PRs for {owner}/{repo}: {response.status_code} {response.text}')
        sys.exit(1)
    return response.json()


def fetch_patch(pr, repo):
    url = pr['patch_url']
    headers = {
        'Accept': 'application/vnd.github.v3.patch'
    }
    response = requests.get(url, headers=headers)
    pfile = f'pr_{pr["number"]}.patch'
    with open(pfile, 'w') as f:
        f.write(response.text)
    return pfile


def apply_patch(pr, repo, push=None):
    # apply patch
    if os.system(f'cd {repo} && git apply ../pr_{pr["number"]}.patch'):
        logging.error(f'Failed to apply patch for PR {pr["number"]}')
    else:
        logging.info(f'Patch applied successfully')
    # Add changes
    os.system(f'cd {repo} && git add .')
    # commit
    os.system(f'cd {repo} && git commit -a -m "Staging PR {pr["number"]}"')


def save_pr_info(pr):
    '''
    For development and debugging purposes, save full PR info to a file
    '''
    with open(f'pr_{pr["number"]}.json', 'w') as f:
        json.dump(pr, f)


def read_users(filename):
    users = []
    if not os.path.exists(filename):
        logging.error(f'File {filename} not found')
        sys.exit(1)
    config = configparser.ConfigParser()
    config.read(filename)
    users_raw = config.get('DEFAULT', 'users')
    users = [user.strip() for user in users_raw.strip().splitlines()]
    users = [user.lower() for user in users]
    return users

def merge_prs(args, users):
    clone_repo(PROJECT, args)
    prs = get_prs(PROJECT, args.repo)
    for pr in prs:
        #save_pr_info(pr)
        logging.info(f'Checking PR {pr["number"]}: `{pr["title"]}` by {pr["user"]["login"]}')
        #print(f'PR: {pr["title"]} ID: {pr["id"]}')
        #print(f'Author: {pr["user"]["login"]}')
        if not pr["user"]["login"].lower() in users:
            logging.error(f'User {pr["user"]["login"]} not allowed to test PRs')
            continue
        #print(f'Labels: {pr["labels"]}')
        # no labels staging-skip?
        if not pr["labels"] or not 'staging-skip' in [label['name'] for label in pr["labels"]]:
            logging.info(f'Processing PR {pr["number"]}')
            pfile = fetch_patch(pr, args.repo)
            patch_files.append(pfile)
            if args.push:
                apply_patch(pr, args.repo, args.branch)
            else:
                apply_patch(pr, args.repo)
            
        else:
            logging.info(f'Skipping PR {pr["number"]} due to staging-skip label')

    if args.push:
        logging.info('Pushing changes to remote')
        r = os.system(f'cd {args.repo} && git push origin HEAD:{args.branch} --force')
        # if git failed - hard fail here
        if r:
            logging.error('Failed to push changes to remote')
            sys.exit(1)


        logging.info('Changes pushed to remote')
    else:
        logging.info('Changes not pushed to remote')


def main():
    parser = argparse.ArgumentParser(description='KernelCI staging v2')
    parser.add_argument('repo', help='GitHub repo to poll')
    parser.add_argument('--push', help='Push changes to staging branch, using push token (github PAT token)')
    parser.add_argument('--branch', help='Staging branch name', default='staging-snapshot')
    parser.add_argument('--userlist', help='File with users allowed to test PRs', default='../data/staging.ini')
    args = parser.parse_args()
    # check repo name is valid
    if not args.repo:
        print('Invalid repo name')
        sys.exit(1)
    if '/' in args.repo:
        print('Invalid repo name')
        sys.exit(1)
    
    if args.userlist:
        users = read_users(args.userlist)
    else:
        logging.error('User list not provided')
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmpdirname:
        merge_prs(args, users)
    
    logging.info('Done')

if __name__ == '__main__':
        main()
