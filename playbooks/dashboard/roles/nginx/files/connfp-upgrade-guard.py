#!/usr/bin/python3
"""Disable connfp before APT installs a different upstream nginx version."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import time

LOAD_CONFIG = Path("/etc/nginx/modules-enabled/50-connfp.conf")
LOG_CONFIG = Path("/etc/nginx/conf.d/00-connfp.conf")
BACKUPS = Path("/var/backups/nginx-connfp")
NGINX_PACKAGES = {"nginx", "nginx-core", "nginx-full", "nginx-light", "nginx-extras"}


def upstream(version):
    match = re.match(r"(?:\d+:)?(\d+\.\d+\.\d+)", version)
    return match.group(1) if match else None


def built_version():
    match = re.search(r"ngx_http_connfp_module-(?:\d+:)?(\d+\.\d+\.\d+)-", LOAD_CONFIG.read_text())
    return match.group(1) if match else None


def disable(reason):
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    moved = []
    try:
        # Remove variable references before removing the module load line.
        for path in (LOG_CONFIG, LOAD_CONFIG):
            if path.exists():
                backup = BACKUPS / f"apt-{time.time_ns()}-{path.name}"
                path.rename(backup)
                moved.append((path, backup))
    except OSError:
        for path, backup in reversed(moved):
            backup.rename(path)
        raise
    print(f"nginx-connfp: disabled fingerprint configuration: {reason}. "
          "Re-run playbooks/dashboard with --tags nginx-fingerprints to rebuild and enable it.",
          file=sys.stderr)


def inspect_packages(paths):
    if not LOAD_CONFIG.exists():
        return
    expected = built_version()
    for path in paths:
        # APT's explicitly selected version-1 hook protocol supplies .deb paths.
        path = path.rstrip("\n")
        if not path:
            continue
        package = subprocess.check_output(["dpkg-deb", "--field", path, "Package"], text=True).strip()
        if package not in NGINX_PACKAGES:
            continue
        version = subprocess.check_output(["dpkg-deb", "--field", path, "Version"], text=True).strip()
        if expected is None or upstream(version) != expected:
            disable(f"APT will install {package} {version}; module was built for {expected or 'unknown'}")
            return


def check_installed():
    if not LOAD_CONFIG.exists():
        return
    expected = built_version()
    result = subprocess.run(["/usr/sbin/nginx", "-v"], capture_output=True, text=True, check=True)
    match = re.search(r"nginx/(\d+\.\d+\.\d+)", result.stderr)
    actual = match.group(1) if match else None
    if expected is None or actual != expected:
        disable(f"installed nginx is {actual or 'unknown'}; module was built for {expected or 'unknown'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check installed nginx after an APT transaction")
    args = parser.parse_args()
    check_installed() if args.check else inspect_packages(sys.stdin)


if __name__ == "__main__":
    main()
