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
echo "Copying systemd services and timers"
cp -v *.service /etc/systemd/system/
cp -v *.timer /etc/systemd/system/


# Stop possible running services and timers, and disable them
echo "Stopping and disabling services and timers"
systemctl stop *.service || true
systemctl stop *.timer || true

# Reload systemd to pick up new/updated services and timers
echo "Reloading systemd"
systemctl daemon-reload

# Enable and start services and timers
echo "Enabling and starting services and timers"
ls -1 *.service | xargs -I {} systemctl enable {}
ls -1 *.timer | xargs -I {} systemctl enable {}

