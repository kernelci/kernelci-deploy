#!/usr/bin/python3
"""Run the upstream integration test only without root and with private paths."""
import os
from pathlib import Path
import subprocess
import sys


def validate(script):
    if os.geteuid() == 0:
        raise RuntimeError("Refusing to run the nginx integration test as root; use an unprivileged SSH user")
    lines = [line.strip() for line in Path(script).read_text().splitlines()]
    for directive, directory in (
        ("client_body_temp_path", "client_body"),
        ("proxy_temp_path", "proxy"),
        ("fastcgi_temp_path", "fastcgi"),
        ("uwsgi_temp_path", "uwsgi"),
        ("scgi_temp_path", "scgi"),
    ):
        expected = f"{directive} {{root}}/{directory};"
        if lines.count(expected) != 1:
            raise RuntimeError(f"Integration test lacks exactly one private temp path: {expected}")


def main():
    script, nginx, module = sys.argv[1:]
    validate(script)
    subprocess.run([sys.executable, script, nginx, module], check=True)


if __name__ == "__main__":
    main()
