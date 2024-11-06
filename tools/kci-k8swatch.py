#!/usr/bin/env python3
"""
Watch docker containers by pattern, save logs to files
./kci-k8swatch.py context /var/log/kci-k8swatch

"""
import os
import re
import sys
import time
import logging
import argparse
import datetime
import threading

# k8s python client
from kubernetes import client, config, watch


PODS = []
CONTEXT = ""


def log_write(log_file, log_line):
    """Write log line to file"""
    with open(log_file, "a") as log_fd:
        logging.info(log_line)
        log_fd.write(log_line + "\n")


def watch_pod(podname, namespace, logdir):
    """Watch pod logs"""
    global current_date, PODS
    log_file = os.path.join(logdir, f"{current_date}-{podname}-{namespace}.log")
    log_write(log_file, f"Watching {podname} {namespace}\n")
    config.load_kube_config(context=CONTEXT)
    v1 = client.CoreV1Api()
    w = watch.Watch()
    for event in w.stream(
        v1.read_namespaced_pod_log, name=podname, namespace=namespace
    ):
        log_line = f"{datetime.datetime.now()} {podname} {namespace} {event}"
        log_write(log_file, log_line)
    w.stop()
    log_write(log_file, f"End of {podname} {namespace}\n")
    PODS.remove(f"{namespace}/{podname}")


def watchloop(context, logdir):
    global PODS
    # Get pods
    config.load_kube_config(context=CONTEXT)
    v1 = client.CoreV1Api()
    try:
        ret = v1.list_pod_for_all_namespaces(watch=False)
    except Exception as e:
        logging.error(f"Error: {e}")
        return
    for i in ret.items:
        pod_name = i.metadata.name
        namespace = i.metadata.namespace
        # is it in PODS list?
        podid = f"{namespace}/{pod_name}"
        if podid in PODS:
            continue
        # check pod status, if running
        print(f"Checking {pod_name} {namespace} {i.status.phase}")
        if i.status.phase != "Running":
            continue
        # then spawn a thread to watch it
        PODS.append(podid)
        t = threading.Thread(target=watch_pod, args=(pod_name, namespace, logdir))
        t.start()


def main():
    """Main function"""
    global current_date, CONTEXT
    parser = argparse.ArgumentParser(description="Watch k8s containers")
    parser.add_argument("context", help="Context to watch")
    parser.add_argument("logdir", help="Log directory")
    args = parser.parse_args()

    if not os.path.exists(args.logdir):
        logging.error(f"Log directory {args.logdir} not exists")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO)
    CONTEXT = args.context
    logging.info(f"Watching {args.context} to {args.logdir}")
    current_date = time.strftime("%Y-%m-%d")
    log_file = os.path.join(args.logdir, f"{current_date}.log")
    with open(log_file, "a") as log_fd:
        log_fd.write(f"Watching {args.context}\n")
    while True:
        watchloop(args.context, args.logdir)
        time.sleep(10)


if __name__ == "__main__":
    main()
