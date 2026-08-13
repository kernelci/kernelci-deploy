#!/usr/bin/env python3
"""
Synchronize read-only Postgres users from the dashboard GitHub team file.

The source of truth is a plain text file in a GitHub repository, the same file
used by ``ssh_key_sync.py``. Each non-empty, non-comment line is one GitHub
username. Every managed login role in the database is compared against that
list:

  * A GitHub user in the list without a matching database role is created. A
    random password is generated, the role is granted read-only access to the
    public schema, the credentials are mailed to the configured recipient, and
    the ``user:password`` pair is appended to ``users.txt``.

  * A managed login role that is no longer in the list is dropped.

Reserved system accounts are never touched. This includes ``kcidb``,
``kcidb_rest``, ``kcidb_ddl``, every role prefixed with ``kcidb_``, the login
role used by this script, Postgres superusers, and internal ``pg_*`` roles.

Database credentials come from the environment, which is loaded from a ``.env``
file so the script can share the one the kcidb-ng stack already uses:

  DB_HOST, DB_USER, DB_PASSWORD, DB_NAME   connection settings
  DB_PORT                                  optional, defaults to 5432
  GITHUB_TOKEN                             optional, raises API rate limits
  TEAM_SYNC_EMAIL_TO                       optional, credential recipient

Real environment variables win over the file, so a value can always be
overridden per invocation. The legacy ``.dbauth`` file (``KEY = value`` lines
with HOST/USER/PASSWORD/DBNAME) is still read when no DB_* settings are
present.

Nothing in this file is a secret: passwords are generated at runtime and
credentials are only ever read from the environment.

Usage:
  # Preview only (default):
  ./postgres_team_sync.py

  # Apply changes, taking the environment from the kcidb-ng stack:
  ./postgres_team_sync.py --env-file /home/azureuser/kcidb-ng/.env --apply
"""

import argparse
import json
import logging
import os
import re
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg2
import psycopg2.sql


# ---------------------------------------------------------------------------
# Configuration. Keep operational config in this file, as requested.
# ---------------------------------------------------------------------------

# GitHub source of truth, one username per line. Blank lines and '#' comments
# are ignored. This is the same file consumed by ssh_key_sync.py.
SOURCE_REPO = "kernelci/dashboard"
SOURCE_PATH = ".github/dashboard-team"
SOURCE_REF = ""  # Optional branch/tag/SHA. Leave empty for the default branch.

# Optional token environment variable. Reading a public file needs no auth, but
# a token raises rate limits and allows reading a private source repo.
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

# Environment file searched when --env-file is not given. The first one that
# exists is loaded; real environment variables always take precedence.
DEFAULT_ENV_FILES = (".env", "/home/azureuser/kcidb-ng/.env")

# Legacy database credentials file (KEY = value lines), read only when the
# environment carries no DB_* settings.
DBAUTH_PATH = ".dbauth"

# Reserved accounts that are never created, modified, or dropped by this script.
RESERVED_USERS = {"kcidb", "kcidb_rest", "kcidb_ddl", "replication"}
RESERVED_PREFIX = "kcidb_"
# Roles whose name ends with any of these suffixes are also reserved, e.g.
# per-developer accounts like "someone@profusion.mobi".
RESERVED_SUFFIXES = ("profusion.mobi",)

# Where newly generated credentials are recorded, one "user:password" per line.
CREDENTIALS_PATH = "users.txt"

# Number of random bytes used to generate a password (base64url encoded).
PASSWORD_BYTES = 24

# Email recipient for newly generated credentials, from TEAM_SYNC_EMAIL_TO.
# Sending itself is a stub. Kept out of the source so this file carries no
# personal data.
EMAIL_TO_ENV = "TEAM_SYNC_EMAIL_TO"


# ---------------------------------------------------------------------------

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "kernelci-dashboard-postgres-team-sync"
MAX_RETRIES = 4

USERNAME_RE = re.compile(r"^[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}$")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("postgres_team_sync")


def die(message):
    """Log a fatal error and terminate the process with a non-zero exit."""
    log.error(message)
    raise SystemExit(1)


def load_env_file(path=None):
    """Load ``KEY=value`` pairs from a .env file into the environment.

    Values already present in the real environment are left alone, so an
    explicit variable always beats the file. Understands the ``export KEY=``
    prefix and single or double quoted values, which is what the kcidb-ng
    stack's .env uses.
    """
    candidates = [path] if path else list(DEFAULT_ENV_FILES)
    for candidate in candidates:
        env_path = Path(candidate)
        if not env_path.is_file():
            continue
        for line_no, line in enumerate(
            env_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("export "):
                text = text[len("export "):].lstrip()
            if "=" not in text:
                log.warning("Ignoring line %d in %s: %r", line_no, env_path, line)
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)
        log.info("Loaded environment from %s", env_path)
        return str(env_path)

    if path:
        die(f"environment file not found: {path}")
    return None


def is_reserved(username):
    """Return whether a role must never be managed by this script."""
    return (
        username in RESERVED_USERS
        or username.startswith(RESERVED_PREFIX)
        or username.endswith(RESERVED_SUFFIXES)
    )


# ---------------------------------------------------------------------------
# GitHub source file
# ---------------------------------------------------------------------------

def github_request(method, path, accept="application/vnd.github+json"):
    """Call the GitHub API and return ``(status, data)`` with retries."""
    url = path if path.startswith("http") else API_BASE + path
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get(GITHUB_TOKEN_ENV)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_err = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, parse_response(raw, accept)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            retryable = exc.code >= 500 or exc.code == 429 or (
                exc.code == 403 and "rate limit" in raw.lower()
            )
            if retryable and attempt < MAX_RETRIES - 1:
                wait = retry_after(exc.headers, attempt)
                log.warning("%s %s -> %s, retrying in %ss", method, url, exc.code, wait)
                time.sleep(wait)
                last_err = exc
                continue
            return exc.code, parse_response(raw, accept)
        except urllib.error.URLError as exc:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                log.warning("%s %s failed (%s), retrying in %ss", method, url, exc, wait)
                time.sleep(wait)
                last_err = exc
                continue
            die(f"{method} {url} failed: {exc}")

    die(f"{method} {url} failed after {MAX_RETRIES} attempts: {last_err}")


def parse_response(raw, accept):
    """Parse an HTTP response body according to the requested Accept header."""
    if not raw:
        return None
    if "json" not in accept:
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def retry_after(headers, attempt):
    """Return the retry delay for a GitHub request attempt."""
    value = headers.get("Retry-After")
    if value and value.isdigit():
        return int(value)
    return 2 ** attempt


def fetch_source_file(source_path):
    """Fetch a raw file from ``SOURCE_REPO`` at the configured ref."""
    owner_repo = SOURCE_REPO.strip("/")
    if owner_repo.count("/") != 1:
        die(f"invalid SOURCE_REPO: {SOURCE_REPO!r}")

    encoded_path = "/".join(
        urllib.parse.quote(part, safe="") for part in source_path.strip("/").split("/")
    )
    url_path = f"/repos/{owner_repo}/contents/{encoded_path}"
    if SOURCE_REF:
        url_path += "?ref=" + urllib.parse.quote(SOURCE_REF, safe="")

    status, content = github_request("GET", url_path, accept="application/vnd.github.raw")
    if status == 404:
        die(f"source file not found: {SOURCE_REPO}:{source_path}")
    if status != 200 or not isinstance(content, str):
        die(f"could not fetch {SOURCE_REPO}:{source_path} (status {status})")
    return content


def fetch_team_usernames():
    """Fetch, validate, normalize, deduplicate, and sort team usernames."""
    content = fetch_source_file(SOURCE_PATH)

    invalid = []
    usernames = set()
    for line_no, line in enumerate(content.splitlines(), start=1):
        username = line.split("#", 1)[0].strip()
        if not username:
            continue
        if not USERNAME_RE.match(username):
            invalid.append(f"line {line_no}: {username!r}")
            continue
        usernames.add(username.lower())

    if invalid:
        die("invalid GitHub username(s): " + ", ".join(invalid))
    if not usernames:
        die(f"refusing to run: {SOURCE_REPO}:{SOURCE_PATH} contains no usernames")

    # A team member that collides with a reserved account is a configuration
    # error: we would otherwise try to manage a protected role.
    reserved = sorted(name for name in usernames if is_reserved(name))
    if reserved:
        die("team file lists reserved account(s): " + ", ".join(reserved))

    return sorted(usernames)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def read_db_config():
    """Return database connection settings from the environment.

    Falls back to the legacy ``.dbauth`` file when the environment carries no
    DB_* settings, so a host that has not been migrated keeps working.
    """
    env_names = {
        "host": "DB_HOST",
        "user": "DB_USER",
        "password": "DB_PASSWORD",
        "dbname": "DB_NAME",
        "port": "DB_PORT",
    }
    values = {key: os.environ.get(name, "") for key, name in env_names.items()}

    missing = [key for key in ("host", "user", "password", "dbname") if not values[key]]
    if not missing:
        return values

    legacy = read_dbauth(required=False)
    if legacy:
        log.warning(
            "Using legacy %s; set DB_HOST/DB_USER/DB_PASSWORD/DB_NAME in the "
            "environment file instead", DBAUTH_PATH
        )
        return legacy

    die(
        "missing database settings: "
        + ", ".join(env_names[key] for key in missing)
        + ". Provide them in the environment or an env file (searched: "
        + ", ".join(DEFAULT_ENV_FILES) + ")"
    )


def read_dbauth(required=True):
    """Parse the legacy ``.dbauth`` credentials file into a connection dict."""
    path = Path(DBAUTH_PATH)
    if not path.exists():
        if not required:
            return None
        die(f"database credentials file not found: {path}")

    values = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            die(f"invalid line in {path} line {line_no}: {line!r}")
        key, value = text.split("=", 1)
        values[key.strip().upper()] = value.strip()

    missing = [key for key in ("HOST", "USER", "PASSWORD", "DBNAME") if not values.get(key)]
    if missing:
        if not required:
            return None
        die(f"{path} is missing required keys: {', '.join(missing)}")

    return {
        "host": values["HOST"],
        "user": values["USER"],
        "password": values["PASSWORD"],
        "dbname": values["DBNAME"],
        "port": values.get("PORT", ""),
    }


def connect(dbauth):
    """Open a database connection or abort on failure."""
    params = {
        "host": dbauth["host"],
        "user": dbauth["user"],
        "password": dbauth["password"],
        "dbname": dbauth["dbname"],
    }
    if dbauth.get("port"):
        params["port"] = dbauth["port"]
    try:
        conn = psycopg2.connect(**params)
    except psycopg2.Error as exc:
        die(f"could not connect to database: {exc}")
    log.info("Connected to database %s on %s", dbauth["dbname"], dbauth["host"])
    return conn


def fetch_managed_roles(conn, connect_user):
    """Return existing login roles that are subject to synchronization.

    Superusers, internal ``pg_*`` roles, reserved ``kcidb`` accounts, and the
    role this script connects as are excluded and never dropped.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rolname
            FROM pg_catalog.pg_roles
            WHERE rolcanlogin
              AND NOT rolsuper
              AND rolname NOT LIKE 'pg\\_%'
            ORDER BY rolname
            """
        )
        roles = [row[0] for row in cur.fetchall()]

    managed = set()
    for role in roles:
        if role == connect_user or is_reserved(role):
            continue
        managed.add(role)
    return managed


def role_exists(cur, username):
    """Return whether a role already exists."""
    cur.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s;", (username,))
    return cur.fetchone() is not None


def create_readonly_user(conn, username, password):
    """Create a login role with read-only access to the public schema."""
    ident = psycopg2.sql.Identifier(username)
    db_ident = psycopg2.sql.Identifier(conn.info.dbname)
    with conn.cursor() as cur:
        if role_exists(cur, username):
            # Should not happen: caller only creates absent users. Reset the
            # password instead of failing so the run stays convergent.
            log.warning("Role %s already exists, resetting password only", username)
            cur.execute(
                psycopg2.sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s;").format(ident),
                (password,),
            )
        else:
            cur.execute(
                psycopg2.sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s;").format(ident),
                (password,),
            )
        cur.execute(
            psycopg2.sql.SQL("GRANT CONNECT ON DATABASE {} TO {};").format(db_ident, ident)
        )
        cur.execute(psycopg2.sql.SQL("GRANT USAGE ON SCHEMA public TO {};").format(ident))
        cur.execute(
            psycopg2.sql.SQL(
                "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {};"
            ).format(ident)
        )
        cur.execute(
            psycopg2.sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {};"
            ).format(ident)
        )
    conn.commit()
    log.info("Created read-only role %s", username)


def drop_user(conn, username):
    """Drop a managed role after removing its privileges and owned objects."""
    if is_reserved(username):
        # Defensive: never drop a protected account, regardless of caller.
        die(f"refusing to drop reserved role: {username}")
    ident = psycopg2.sql.Identifier(username)
    with conn.cursor() as cur:
        if not role_exists(cur, username):
            log.info("Role %s no longer exists", username)
            return
        cur.execute(psycopg2.sql.SQL("DROP OWNED BY {} CASCADE;").format(ident))
        cur.execute(psycopg2.sql.SQL("DROP ROLE {};").format(ident))
    conn.commit()
    log.info("Dropped role %s", username)


# ---------------------------------------------------------------------------
# Credential delivery
# ---------------------------------------------------------------------------

def generate_password():
    """Generate a high-entropy password suitable for one-time delivery."""
    return secrets.token_urlsafe(PASSWORD_BYTES)


def send_credentials_email(username, password):
    """Stub: email new credentials to the configured recipient.

    TODO: wire this up to a real SMTP transport. For now it only records the
    intent so the rest of the flow can be exercised without a mail server.
    """
    recipient = os.environ.get(EMAIL_TO_ENV, "")
    if not recipient:
        log.warning(
            "%s is not set, credentials for %s were only written to %s",
            EMAIL_TO_ENV, username, CREDENTIALS_PATH
        )
        return
    log.info("TODO: email credentials for %s to %s (not sent)", username, recipient)


def append_credentials(username, password):
    """Append a ``user:password`` line to the credentials file (mode 0600)."""
    path = Path(CREDENTIALS_PATH)
    existed = path.exists()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{username}:{password}\n")
    if not existed:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    log.info("Recorded credentials for %s in %s", username, path)


# ---------------------------------------------------------------------------
# Synchronization
# ---------------------------------------------------------------------------

def sync(conn, team, dry_run):
    """Reconcile database login roles with the GitHub team list."""
    connect_user = conn.info.user
    existing = fetch_managed_roles(conn, connect_user)

    to_create = sorted(set(team) - existing)
    to_drop = sorted(existing - set(team))

    log.info("Team users: %d", len(team))
    log.info("Managed roles in database: %d", len(existing))
    log.info("To create: %d", len(to_create))
    log.info("To drop: %d", len(to_drop))

    if dry_run:
        for username in to_create:
            log.info("DRY-RUN: would create read-only role %s", username)
        for username in to_drop:
            log.info("DRY-RUN: would drop role %s", username)
        return

    for username in to_create:
        password = generate_password()
        create_readonly_user(conn, username, password)
        append_credentials(username, password)
        send_credentials_email(username, password)

    for username in to_drop:
        drop_user(conn, username)


def parse_args():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Sync read-only Postgres users from the dashboard GitHub team file."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply changes; default is a dry-run preview",
    )
    parser.add_argument(
        "--env-file",
        help="environment file to load; default searches "
             + ", ".join(DEFAULT_ENV_FILES),
    )
    return parser.parse_args()


def main():
    """Command-line entry point."""
    args = parse_args()
    load_env_file(args.env_file)

    team = fetch_team_usernames()
    dbauth = read_db_config()
    conn = connect(dbauth)
    try:
        sync(conn, team, dry_run=not args.apply)
    finally:
        conn.close()
        log.info("Database connection closed")


if __name__ == "__main__":
    main()
