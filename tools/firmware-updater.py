#! /usr/bin/env python3
"""
Once per hour:
- check upstream HEAD with git ls-remote, skip if unchanged
- git clone https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git --depth 1
- tar -czf into a temporary file in DSTDIR
- atomically rename it to DSTDIR/linux-firmware.tar.gz, so readers never
  see a partial file
"""

import os
import subprocess
import time
import tempfile
import sys

FIRMWARE_GIT = (
    "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git"
)
DSTDIR = "/data/storage.kernelci.org"
TARBALL = "linux-firmware.tar.gz"
# Upstream commit the published tarball was built from
HEAD_FILE = os.path.join(DSTDIR, ".linux-firmware.head")
CHECK_INTERVAL = 60 * 60
SYSTEMD_SERVICE_FILE = "/etc/systemd/system/firmware-updater.service"


def create_systemd_service():
    # create systemd service file
    with open(SYSTEMD_SERVICE_FILE, "w") as f:
        f.write(
            """
[Unit]
Description=Firmware Updater

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/firmware-updater.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
        )
    # reload systemd
    subprocess.run(["systemctl", "daemon-reload"])


def install_systemd_service():
    # check if file installed in /usr/local/bin
    if not os.path.exists("/usr/local/bin/firmware-updater.py"):
        print("Firmware updater not found in /usr/local/bin, please install it first")
        sys.exit(1)
    # check if systemd-service exists
    if not os.path.exists(SYSTEMD_SERVICE_FILE):
        create_systemd_service()

    # enable and start systemd service
    subprocess.run(["systemctl", "enable", "firmware-updater"])
    subprocess.run(["systemctl", "start", "firmware-updater"])


def signal_sysadmins(message):
    print(message)
    # TODO: send email to sysadmins


def get_upstream_head():
    r = subprocess.run(
        ["git", "ls-remote", FIRMWARE_GIT, "HEAD"], capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout:
        print(f"Error checking linux-firmware HEAD: {r.returncode} {r.stderr}")
        return None
    return r.stdout.split()[0]


def get_published_head():
    if not os.path.exists(os.path.join(DSTDIR, TARBALL)):
        return None
    try:
        with open(HEAD_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


def update_firmware(temp_dir):
    # use temp_dir as working directory
    os.chdir(temp_dir)

    # check if DSTDIR exists
    if not os.path.exists(DSTDIR):
        sys.exit(1)

    head = get_upstream_head()
    if head and head == get_published_head():
        print(f"linux-firmware unchanged at {head}, skipping")
        return

    # update firmware
    r = subprocess.run(["git", "clone", FIRMWARE_GIT, "--depth", "1"])
    if r.returncode != 0:
        print(f"Error cloning linux-firmware: {r.returncode}")
        signal_sysadmins(f"Error cloning linux-firmware: {r.returncode}")
        return
    # Build on the destination filesystem so the final rename is atomic
    tmp_tarball = os.path.join(DSTDIR, f".{TARBALL}.tmp")
    r = subprocess.run(["tar", "-czf", tmp_tarball, "linux-firmware"])
    if r.returncode != 0:
        print(f"Error tarring linux-firmware: {r.returncode}")
        signal_sysadmins(f"Error tarring linux-firmware: {r.returncode}")
        if os.path.exists(tmp_tarball):
            os.remove(tmp_tarball)
        return
    os.chmod(tmp_tarball, 0o644)
    try:
        os.replace(tmp_tarball, os.path.join(DSTDIR, TARBALL))
    except OSError as e:
        print(f"Error moving linux-firmware.tar.gz: {e}")
        signal_sysadmins(f"Error moving linux-firmware.tar.gz: {e}")
        return
    os.system("rm -rf linux-firmware")
    if head:
        with open(HEAD_FILE, "w") as f:
            f.write(head + "\n")
    print(f"linux-firmware updated to {head}")


if __name__ == "__main__":
    # check root
    if os.geteuid() != 0:
        print("This script must be run as root")
        sys.exit(1)
    # check if systemd-service exists
    if not os.path.exists(SYSTEMD_SERVICE_FILE):
        print("Firmware updater service not found, creating it")
        install_systemd_service()
        sys.exit(0)
    while True:
        # create temporary directory where we operate
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"Using temporary directory: {temp_dir}")
            update_firmware(temp_dir)
        time.sleep(CHECK_INTERVAL)
