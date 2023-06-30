#!/bin/bash -e

# This script install KernelCI deploy systemd services and timers

# Check if running as root
userid=$(id -u)
if [ "$userid" -ne 0 ]; then
    echo "Please run this command as root"
    exit 1
fi

# argument should be either production or staging
if [ $# -ne 1 ]; then
    echo "Usage: $0 <production|staging>"
    exit 1
fi

# cd to script directory/<production|staging>
cd "$(dirname "$0")/$1"
if [ $? -ne 0 ]; then
    echo "Failed to cd to $(dirname "$0")/$1"
    exit 1
fi

# Copy systemd services and timers
cp -v *.service /etc/systemd/system/
cp -v *.timer /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

# Enable and start services and timers
ls -1 *.service | xargs -I {} systemctl enable {}.service
ls -1 *.timer | xargs -I {} systemctl enable {}.timer

