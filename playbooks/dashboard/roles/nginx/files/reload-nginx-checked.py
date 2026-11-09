#!/usr/bin/python3
"""Reload nginx and require a stable new worker generation before succeeding."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time


def workers(master):
    result = set()
    children = Path(f"/proc/{master}/task/{master}/children").read_text().split()
    for child in children:
        try:
            proc = Path("/proc") / child
            title = (proc / "cmdline").read_bytes().split(b"\0", 1)[0].rstrip()
            # Old workers may drain requests for a long time after an accepted
            # reload; their title changes to 'worker process is shutting down'.
            if title == b"nginx: worker process":
                # Include starttime to distinguish PID reuse.
                started = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
                result.add((int(child), started))
        except FileNotFoundError:
            continue
    return result


def reload_checked(pid_file, timeout, *, send_signal=False):
    master = int(Path(pid_file).read_text().strip())
    if master <= 1:
        raise RuntimeError("Invalid nginx master PID")
    before = workers(master)
    if not before:
        raise RuntimeError("No live nginx workers before reload")
    if send_signal:
        # Used by the isolated regression test; mirrors Debian's ExecReload.
        os.kill(master, signal.SIGHUP)
    else:
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
    deadline = time.monotonic() + timeout
    stable = set()
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        if int(Path(pid_file).read_text().strip()) != master:
            raise RuntimeError("nginx master changed during a graceful reload")
        current = workers(master)
        fresh = current - before
        # One respawned worker alongside an old active generation is not proof
        # of a reload. This role does not change worker_processes.
        if current & before or len(fresh) < len(before):
            fresh = set()
        if fresh != stable:
            stable, stable_since = fresh, time.monotonic()
        if stable and time.monotonic() - stable_since >= 1:
            print(f"nginx master {master} accepted reload; new workers: {sorted(p for p, _ in stable)}")
            return
        time.sleep(0.1)
    raise RuntimeError(f"nginx did not establish a stable new worker generation within {timeout}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid-file", default="/run/nginx.pid")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--signal", action="store_true", help="Signal an isolated test master instead of systemd")
    args = parser.parse_args()
    reload_checked(args.pid_file, args.timeout, send_signal=args.signal)


if __name__ == "__main__":
    main()
