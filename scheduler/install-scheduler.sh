#!/bin/bash -e

# This script install KernelCI deploy systemd services and timers

# Check if running as root
UID=$(id -u)
if [ "$UID" -ne 0 ]; then
    echo "Please run this command as root"
    exit 1
fi

# cd to script directory
cd "$(dirname "$0")"

# Copy systemd services and timers
cp -v *.service /etc/systemd/system/
cp -v *.timer /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

# Enable and start services and timers
ls -1 *.service | xargs -I {} systemctl enable {}.service
ls -1 *.timer | xargs -I {} systemctl enable {}.timer

