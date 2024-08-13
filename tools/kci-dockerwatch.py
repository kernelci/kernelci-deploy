#!/usr/bin/env python3
'''
Watch docker containers by pattern, save logs to files
./kci-dockerwatch.py --exclude ".*jenkins.*" "kernelci.*" /data/storage.staging.kernelci.org/docker-logs/

'''
import os
import re
import sys
import time
import docker
import logging
import argparse
import threading

active_containers = []
#finished_containers = []
thread = {}
current_date = time.strftime('%Y-%m-%d', time.localtime())
tlock = threading.Lock()
crash_keywords=['Traceback (most recent call last)']


def message_bot(msg):
    os.system(f'./kci-slackbot.py --message "{msg}"')


def container_logger_thread(container, logpath):
    '''Container logger thread'''
    active_containers.append(container.id)
    with open(logpath, 'a') as logfile:
        for line in container.logs(stream=True):
            # detect crash keywords, lowercase both
            if any(keyword.lower() in line.decode('utf-8').lower() for keyword in crash_keywords):
                logging.error(f'Crash detected in container: {container.name} id: {container.id}')
                message_bot(f'Crash detected in container: {container.name} id: {container.id}')
            logfile.write(line.decode('utf-8'))
    with tlock:
        active_containers.remove(container.id)
    #finished_containers.append(container.id)


def get_containers_by_pattern(client, pattern, exclude=None):
    '''Get containers by pattern'''
    containers = client.containers.list()
    matched = []
    for container in containers:
        if container.status != 'running':
            continue
        if re.match(pattern, container.name):
            if exclude and re.match(exclude, container.name):
                continue
            matched.append(container)
    return matched


def watch_containers(client, args):
    '''Watch containers by pattern'''
    pattern = args.pattern
    logdir = args.logdir
    exclude = args.exclude
    containers = get_containers_by_pattern(client, pattern, exclude)
    for container in containers:
        if container.id in active_containers:
            continue
        print(f'Watching container: {container.name} id: {container.id}')
        logname = f'{container.name}-{container.id}.log'
        datedir = time.strftime('%Y-%m-%d', time.localtime())
        if not os.path.exists(os.path.join(logdir, datedir)):
            os.makedirs(os.path.join(logdir, datedir))
        logpath = os.path.join(logdir, datedir, logname)
        with tlock:
            threads_to_die = []
            thread[container.id] = threading.Thread(target=container_logger_thread, args=(container, logpath))
            thread[container.id].start()
            # join finished threads
            for key, value in thread.items():
                if not value.is_alive():
                    value.join()
                    threads_to_die.append(key)
            for key in threads_to_die:
                del thread[key]


def main():
    '''Main function'''
    global current_date
    parser = argparse.ArgumentParser(description='Watch docker containers by pattern')
    parser.add_argument('pattern', help='Pattern to match container names')
    parser.add_argument('logdir', help='Directory to save logs')
    parser.add_argument('--exclude', help='Pattern to exclude container names')
    args = parser.parse_args()

    if not os.path.exists(args.logdir):
        logging.error(f'Log directory {args.logdir} not exists')
        sys.exit(1)
    logging.basicConfig(level=logging.INFO)
    client = docker.from_env()
    while True:
        watch_containers(client, args)
        # if date changes, clear active containers, so that logs will be saved to new files
        # TODO: We will have some info duplicated in logs, need to fix it
        if time.strftime('%Y-%m-%d', time.localtime()) != current_date:
            active_containers.clear()
            current_date = time.strftime('%Y-%m-%d', time.localtime())
        time.sleep(1)

if __name__ == '__main__':
    main()