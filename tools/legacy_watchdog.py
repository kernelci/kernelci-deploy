#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# Copyright (C) 2024, Collabora Ltd.
# Author: Denys Fedoryshchenko <denys.f@collabora.com>
'''
This script used to keep legacy KernelCI alive.
In case celery queue grows abnornally,
and doesn't go down for a while -
it will restart relevant services as last resort.
Yes, this is not a proper bugfix, but in current
circumstances it's better than nothing.
'''

import subprocess
import redis
import time
import os

# Restart services if queue is over 100 for 50 seconds (5*10)
MAX_BAD_STATE = 10
# Check queue every 5 seconds
TIME_INTERVAL = 5


def la_check():
    '''
    Check number of processors and if
    la > num_proc, return True
    '''
    with open('/proc/loadavg', 'r') as f:
        la = f.read().split()[0]
    num_proc = os.cpu_count()
    if float(la) > num_proc:
        return True
    return False

def get_celery_queue_length():
    r = redis.Redis(host='localhost', port=6379, db=0)
    return r.llen('celery')


def restart_services():
    subprocess.run(['systemctl', 'restart', 'kernelci-backend'])
    subprocess.run(['systemctl', 'restart', 'kernelci-celery'])
    subprocess.run(['systemctl', 'restart', 'kernelci-celery-beat'])


def main():
    max = 0
    bad_state = 0
    while True:
        time.sleep(TIME_INTERVAL)
        # If LA too high, skip checks
        if la_check():
            continue
        length = get_celery_queue_length()
        # Queue is empty or decreasing
        if length == 0 or length < max:
            bad_state = 0
            continue
        # Queue is growing
        if length > max:
            max = length
            print("Max queue length: %d" % max)
        # Queue is over normal
        if length > 100:
            bad_state += 1
        if bad_state > MAX_BAD_STATE:
            print("Restarting services")
            restart_services()
            bad_state = 0
