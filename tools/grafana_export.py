#!/usr/bin/env python3
"""
Export Grafana dashboards from a Grafana SQLite database.

Dashboards built in the Grafana UI live only in grafana.db. On the KernelCI
monitoring host that database sits inside a Docker named volume, so the
dashboards exist in exactly one copy, on one VM, with no backup. This script
reads them out so they can be kept in git and provisioned onto a rebuilt host.

It opens the database read-only, so it is safe to run against a live Grafana.

Usage, on the monitoring host:

  # write one .json per dashboard, named after its title
  sudo ./grafana_export.py -o ./dashboards

  # or stream everything as a single JSON document
  sudo ./grafana_export.py

Usage, from a workstation, without copying the script over:

  ssh kernelci@mon.kernelci.org 'sudo python3' < tools/grafana_export.py > dashboards.json

The default database path is the Docker volume used by the monitoring stack;
override it with --db for any other deployment.
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "/var/lib/docker/volumes/monitoring_grafanadata/_data/grafana.db"

QUERY = """
    select d.uid, d.title, coalesce(f.title, ''), d.version, d.data
    from dashboard d
    left join dashboard f on f.id = d.folder_id
    where d.is_folder = 0
    order by d.title
"""


def slugify(title):
    """Return a filename-safe form of a dashboard title."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "dashboard"


def read_dashboards(db_path):
    """Read every dashboard out of a Grafana database, read-only."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"cannot open {db_path}: {exc}")

    out = []
    for uid, title, folder, version, data in con.execute(QUERY):
        try:
            body = json.loads(data)
        except ValueError as exc:
            print(f"skipping {title!r}: unreadable JSON ({exc})", file=sys.stderr)
            continue
        # The numeric id is local to this database and must not be carried to
        # another instance; the uid is what identifies a dashboard.
        body.pop("id", None)
        body["uid"] = uid
        out.append({"uid": uid, "title": title, "folder": folder,
                    "version": version, "data": body})
    return out


def write_files(dashboards, dest):
    """Write one pretty-printed .json per dashboard into a directory."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for entry in dashboards:
        path = dest / f"{slugify(entry['title'])}.json"
        path.write_text(
            json.dumps(entry["data"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"{path} ({entry['uid']})", file=sys.stderr)
    print(f"{len(dashboards)} dashboard(s) written to {dest}", file=sys.stderr)


def parse_args():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Export Grafana dashboards from a Grafana SQLite database."
    )
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"path to grafana.db (default: {DEFAULT_DB})")
    parser.add_argument("-o", "--output",
                        help="directory to write one file per dashboard; "
                             "without it, a single JSON document goes to stdout")
    return parser.parse_args()


def main():
    """Command-line entry point."""
    args = parse_args()
    dashboards = read_dashboards(args.db)
    if not dashboards:
        raise SystemExit(f"no dashboards found in {args.db}")
    if args.output:
        write_files(dashboards, args.output)
    else:
        json.dump(dashboards, sys.stdout)


if __name__ == "__main__":
    main()
