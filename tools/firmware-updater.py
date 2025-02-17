#! /usr/bin/env python3
"""
Once per 6 hours:
- git clone https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git --depth 1
- tar -czvf linux-firmware.tar.gz linux-firmware
- move file to DSTDIR
"""

import os
import subprocess
import time
import tempfile
import sys

DSTDIR = "/data/storage.kernelci.org"
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


def update_firmware(temp_dir):
    # use temp_dir as working directory
    os.chdir(temp_dir)

    # check if DSTDIR exists
    if not os.path.exists(DSTDIR):
        sys.exit(1)

    # update firmware
    r = subprocess.run(
        [
            "git",
            "clone",
            "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git",
            "--depth",
            "1",
        ]
    )
    if r.returncode != 0:
        print(f"Error cloning linux-firmware: {r.returncode}")
        signal_sysadmins(f"Error cloning linux-firmware: {r.returncode}")
        return
    r = subprocess.run(["tar", "-czf", "linux-firmware.tar.gz", "linux-firmware"])
    if r.returncode != 0:
        print(f"Error tarring linux-firmware: {r.returncode}")
        signal_sysadmins(f"Error tarring linux-firmware: {r.returncode}")
        return
    r = subprocess.run(["mv", "linux-firmware.tar.gz", DSTDIR])
    if r.returncode != 0:
        print(f"Error moving linux-firmware.tar.gz: {r.returncode}")
        signal_sysadmins(f"Error moving linux-firmware.tar.gz: {r.returncode}")
        return


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
        time.sleep(6 * 60 * 60)
