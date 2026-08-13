#!/usr/bin/env python3
"""
Export Uptime Kuma monitors from its SQLite database, without secrets.

Uptime Kuma monitors are created in the web UI and live only in kuma.db, so
they share the problem the Grafana dashboards had: one copy, on one host, with
no backup. This script reads them out so the list of what is watched can be
kept in git.

Secrets are excluded by allowlist, not by denylist. The monitor table has 77
columns and several carry credentials in fields whose names do not say so:
database_connection_string holds a full postgres:// URI including the
password, grpc_metadata and kafka_producer_sasl_options can carry tokens, and
so on. Only the fields named in SAFE_FIELDS below are exported; every other
field that is set is reported by name only, so a restore knows what still has
to be filled in by hand.

The notification and user tables are never read: they hold webhook URLs,
tokens and password hashes.

The database is opened read-only, so this is safe to run against a live
instance.

Usage:

  sudo ./uptime_kuma_export.py --db /srv/monitoring/kuma/kuma.db
  ssh dashboard 'sudo python3' < tools/uptime_kuma_export.py > monitors.json
"""

import argparse
import json
import sqlite3
import sys

DEFAULT_DB = "/srv/monitoring/kuma/kuma.db"

# Everything exported. Anything not listed here is treated as potentially
# sensitive and is reported by name only.
SAFE_FIELDS = [
    "name",
    "type",
    "active",
    "url",
    "hostname",
    "port",
    "interval",
    "retry_interval",
    "resend_interval",
    "maxretries",
    "maxredirects",
    "timeout",
    "method",
    "accepted_statuscodes_json",
    "expiry_notification",
    "ignore_tls",
    "upside_down",
    "description",
    "dns_resolve_type",
    "dns_resolve_server",
    "docker_container",
    "docker_host",
    "packet_size",
    "weight",
    "parent",
]

# Fields that are known credential carriers. Listing them explicitly keeps the
# reporting honest even when a future Uptime Kuma adds more columns: anything
# unknown is excluded anyway, and these are called out by name.
# Values that mean "not configured". Uptime Kuma writes defaults into several
# of the fields below, and flagging those would make the report meaningless.
EMPTY_VALUES = (None, "", 0, "null", "{}", '{"mechanism":"None"}')

KNOWN_SECRET_FIELDS = {
    "basic_auth_pass",
    "basic_auth_user",
    "database_connection_string",
    "grpc_metadata",
    "headers",
    "body",
    "kafka_producer_sasl_options",
    "mqtt_password",
    "mqtt_username",
    "oauth_client_id",
    "oauth_client_secret",
    "push_token",
    "radius_password",
    "radius_secret",
    "tls_cert",
    "tls_key",
}


def export(db_path):
    """Return the monitors, carrying only allowlisted fields."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"cannot open {db_path}: {exc}")

    columns = [c[1] for c in con.execute("pragma table_info(monitor)")]
    safe = [c for c in SAFE_FIELDS if c in columns]
    rows = con.execute(f"select {', '.join(columns)} from monitor order by name")

    monitors = []
    for row in rows:
        record = dict(zip(columns, row))
        entry = {k: record[k] for k in safe}
        # Only report credential-carrying fields that are actually set.
        # Listing every unexported column would bury the one that matters.
        withheld = sorted(
            k for k in KNOWN_SECRET_FIELDS
            if record.get(k) not in EMPTY_VALUES
        )
        if withheld:
            entry["_secrets_set_on_host"] = withheld
        monitors.append(entry)
    return monitors


def parse_args():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Export Uptime Kuma monitors without secrets.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"path to kuma.db (default: {DEFAULT_DB})")
    return parser.parse_args()


def main():
    """Command-line entry point."""
    args = parse_args()
    monitors = export(args.db)
    json.dump(monitors, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    print(f"{len(monitors)} monitor(s) exported from {args.db}", file=sys.stderr)


if __name__ == "__main__":
    main()
