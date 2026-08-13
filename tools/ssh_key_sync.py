#!/usr/bin/env python3
"""
Synchronize one local user's SSH authorized_keys from GitHub users.

The username source is a plain text file in a GitHub repository. Each non-empty,
non-comment line is treated as one GitHub username. For each username, this
script fetches the public SSH keys from GitHub and rewrites the configured
local user's authorized_keys file.

The generated authorized_keys file is fully managed by this script. Existing
content is backed up before replacement, then overwritten atomically.

Optionally, this script can also manage Postgres role passwords from
``.github/postgres_users``. That file contains only user/role/version metadata;
passwords are generated on the server, stored in a root-only state file, applied
with ``psql``, and emailed to configured team leadership only when created or
rotated.

Operational settings come from the environment, which is loaded from a ``.env``
file so this can share the one the kcidb-ng stack already uses. Every setting
below has a default in this file; the environment only overrides it:

  GITHUB_TOKEN          optional, raises API rate limits, reads private repos
  DISCORD_WEBHOOK_URL   optional, notify after authorized_keys changes
  POSTGRES_HOST/PORT/USER/DATABASE   psql connection, empty means peer auth
  POSTGRES_PASSWORD     optional, passed to psql as PGPASSWORD
  POSTGRES_EMAIL_TO     comma separated recipients for rotated passwords
  SMTP_PASSWORD         optional, only when SMTP auth is configured

Real environment variables win over the file. No secret is stored in this
file: passwords are generated at runtime and everything else is read from the
environment.

Usage:
  # Preview only:
  ./ssh_key_sync.py

  # Rewrite authorized_keys:
  sudo ./ssh_key_sync.py --apply

  # Install/update this script plus a systemd service and timer:
  sudo ./ssh_key_sync.py --install
"""

import argparse
import json
import logging
import os
import pwd
import re
import secrets
import shutil
import smtplib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration. Keep operational config in this file, as requested.
# ---------------------------------------------------------------------------

# Local Unix account whose ~/.ssh/authorized_keys will be rewritten.
TARGET_USER = "nuvemsql"

# GitHub source of truth, one username per line. Blank lines and '#' comments
# are ignored.
SOURCE_REPO = "kernelci/dashboard"
SOURCE_PATH = ".github/dashboard-team"
SOURCE_REF = ""  # Optional branch/tag/SHA. Leave empty for the default branch.

# Optional token environment variable. Public GitHub keys do not require auth,
# but a token raises rate limits and allows reading a private source repo.
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"

# Environment file searched when --env-file is not given. The first one that
# exists is loaded; real environment variables always take precedence.
DEFAULT_ENV_FILES = (".env", "/home/azureuser/kcidb-ng/.env")

# Optional Discord webhook URL, from DISCORD_WEBHOOK_URL. Leave unset to
# disable notifications. If set, a message is sent only after authorized_keys
# is actually changed.
DISCORD_WEBHOOK_URL = ""
DISCORD_USERNAME = "dashboard-ssh-key-sync"

# Optional Postgres password management. The users file must contain metadata
# only, never plaintext passwords. Supported line forms:
#   github-user role=dashboard_user password_version=1
#   github-user dashboard_user 1
POSTGRES_SYNC_ENABLED = False
POSTGRES_USERS_PATH = ".github/postgres_users"
POSTGRES_STATE_PATH = "/var/lib/dashboard-ssh-key-sync/postgres-passwords.json"
POSTGRES_ROLE_PREFIX = "dashboard_"
POSTGRES_PASSWORD_BYTES = 24

# psql connection. Leave host/user/password empty to use local peer auth.
POSTGRES_PSQL = "/usr/bin/psql"
POSTGRES_DATABASE = "postgres"
POSTGRES_HOST = ""
POSTGRES_PORT = ""
POSTGRES_USER = ""
POSTGRES_PASSWORD_ENV = "POSTGRES_PASSWORD"
POSTGRES_CREATE_ROLES = True
POSTGRES_ROLE_OPTIONS = "LOGIN"
POSTGRES_DISABLE_REMOVED_ROLES = True

# Email generated/rotated Postgres passwords to team leadership. Passwords are
# sent only for users whose password was newly created or rotated.
POSTGRES_EMAIL_TO = []
POSTGRES_EMAIL_FROM = "dashboard-ssh-key-sync@localhost"
POSTGRES_SMTP_HOST = "localhost"
POSTGRES_SMTP_PORT = 25
POSTGRES_SMTP_USE_TLS = False
POSTGRES_SMTP_USERNAME = ""
POSTGRES_SMTP_PASSWORD_ENV = "SMTP_PASSWORD"

# Where --install copies this script on the server.
INSTALL_PATH = "/usr/local/sbin/dashboard-ssh-key-sync.py"

# systemd names and schedule used by --install.
SYSTEMD_SERVICE_NAME = "dashboard-ssh-key-sync.service"
SYSTEMD_TIMER_NAME = "dashboard-ssh-key-sync.timer"
SYSTEMD_ON_BOOT = "5min"
SYSTEMD_INTERVAL = "1h"
SYSTEMD_RANDOM_DELAY = "10min"

# Static keys always included in the generated authorized_keys, regardless of
# the GitHub team file. Each entry is a full "type data [comment]" public key.
# These are subject to the same validation and de-duplication as GitHub keys.
#
# The trailing comment is cosmetic: keys are matched and de-duplicated on the
# "type data" part alone, and the comment is only copied through for
# readability. Keep it to a short identifier rather than a personal email
# address, so this file carries no personal data.
STATIC_AUTHORIZED_KEYS = [
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQC7kEBHVLHxwHcKoL/Rgm8BUE6Kvvg5BveSXlaKEFQYHyNhg8pCmOP3M/pA3KFrz5Cz4x4T3/KJHukjCdxdfL7c3uFZt3HGbRKnjBdgjIZRKUV83IjS40eNR8YTpitIhxK4BqyVWElrGceOpZ1iaM3MHiK6QmE0wSTS5XvrFGFwjR3cZ+vUcyMY++QTHpHe7//CI5n6i3HjaLtpJ9R2dqc6evuwrppSu4Tvhf8jj96SuOqqyZ+HjmLoAA8le/mgG05gs4EVdiM0yknbXd2oc8cXD70QoxoZelaO2L/q1JdwQjJGw9rClfaKLtVaRhbHu7Gk/vz9Wqv4miX9PmUHwuTMU8Jly/AVT2GaR0/+p+97daS0vYK6SUoGumMT4MN2LB+07rxBttgRKUIXiiaVBQGYrBWsXOZarAqgMnAWG98fJ1Jf8lO/9uuwhfBlZY/cGcKlwm5jICA3eNs9jruwBEWhb113sIF0rGPbG5YioQqvdgYr64+t+OcpR87bNuwp/d+Tw/APkBbXYAQAh210A3Xtcl/qHo+CY+rIQYQGAEvyZGaVj83CgJK0nSTR58H0IoraGTLDgahoAnBWgbmzXdp7PA2YLBRD5DcGV9GnGCtV6nNQaTJXDLmIeJj+ppYjbSCX75RlaxJj3Gf6HiN152wssfzcR2Mr64Ec92Avx769Pw== lucas",
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQCzt7KhQUWPSFaA87EkT778GLE4PCjSnsFClUOHB3lLNu8B9tbEu53nanOwvS1gQzvJKwYke7BhSwWV5OmCJqbCukjBD8K1TcmkKbzhab2SjQZ//PBoHxL9o7NhXq/RlTaOiWpLJmp84+nwG4LbshJM5sM2F7EOwFbbKq/f627FocA3oI9jJIMwAgZs6CB6PEAA3zjwek6KUuYUqdNfFrww5iXp7Pw3MyilY73OB1FNrju7TYhMVyqE0G7Sx/97kKGJpBnK4qjzwOHhs2GTjV4BmuPoY0uiEYDZJcAO+FdjG83zOm8/NupJKQqZvQYByhvzwkCqMHYNfl6mL8cdRycIyz01Ae/HglpRMJ7PTuusgvv8YYC4e9p/mvZs1c/8spNAMztAvaxsYWdlHfkJb07A/RvijFJ7fKZ5q6U0FC1yLQ8gJvBIXGBLttTzBKQj7urSbvHgnR9XLSIpRZ0bY4LhSPuctQqQIpCky0RxXE+AQi/ljRk90PY9r5yZ6Fg1BQ3v3HG5MhlMW/zUMnia+CUwcqI/PI35IBZ6HGlBGJrLxwOQ7mVb/4n8s5R6zkwUciq6pZ8XQLxE9jA6nfIJKfjpRZD2AZwEy65/GDsqjgcMPIWm7L7TOjiqmZqzFt47c1UZksV0ExuuqLC056zDBCNrz8EXznPErI7C+vLXHDELGQ== denys",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFqpPpoQdElNPk5Esb6ZtqLkDDykPK48vXImb2OOTZSC marcelo",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOb6tihDMn3FUYS64UUwaKentEocIpIuoS8FDqphI3M9 gustavo",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJxj+xN/dY9QdvCTLf0u7O4xz250i/yNJgAYHy6liytG alanpeixinho@alanpeixinho",
]

# Refuse to replace authorized_keys if the generated key count is suspiciously
# small. Set to 0 to disable.
MIN_KEYS_REQUIRED = 1

# Accepted key algorithms. This intentionally includes modern OpenSSH security
# key formats and RSA for compatibility with existing GitHub user keys.
ALLOWED_KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}


# ---------------------------------------------------------------------------

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "kernelci-dashboard-ssh-key-sync"
MAX_RETRIES = 4

USERNAME_RE = re.compile(r"^[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}$")
KEY_DATA_RE = re.compile(r"^[A-Za-z0-9+/]+={0,3}$")
POSTGRES_ROLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
POSTGRES_VERSION_RE = re.compile(r"^[1-9][0-9]{0,8}$")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("ssh_key_sync")


def die(message):
    """Log a fatal error and terminate the process with a non-zero exit."""
    log.error(message)
    sys.exit(1)


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


def cfg(name, default=""):
    """Return an environment override for a configuration constant."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def cfg_list(name, default=None):
    """Return a comma separated environment override as a list."""
    value = os.environ.get(name)
    if value in (None, ""):
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


def github_request(method, path, accept="application/vnd.github+json"):
    """Call the GitHub API and return ``(status, data, headers)``.

    ``path`` may be an API-relative path or a full URL from a pagination Link
    header. Retryable server, rate-limit, and network failures are retried with
    exponential backoff. Non-retryable HTTP errors are returned to the caller so
    endpoint-specific handling, such as 404 checks, can stay local.
    """
    if path.startswith("http"):
        url = path
    else:
        url = API_BASE + path

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
                return resp.status, parse_response(raw, accept), dict(resp.headers)
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
            return exc.code, parse_response(raw, accept), dict(exc.headers)
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


def get_paginated(path):
    """Fetch all pages from a GitHub list endpoint and return one list."""
    sep = "&" if "?" in path else "?"
    url = API_BASE + path + f"{sep}per_page=100"
    results = []

    while url:
        status, data, headers = github_request("GET", url)
        if status != 200 or not isinstance(data, list):
            die(f"GET {url} returned {status}: {data}")
        results.extend(data)
        url = next_link(headers.get("Link"))

    return results


def next_link(link_header):
    """Extract the ``rel=next`` URL from a GitHub Link header, if present."""
    if not link_header:
        return None
    for part in link_header.split(","):
        bits = part.split(";")
        if len(bits) < 2:
            continue
        target = bits[0].strip().strip("<>")
        if any(bit.strip() == 'rel="next"' for bit in bits[1:]):
            return target
    return None


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

    status, content, _ = github_request(
        "GET",
        url_path,
        accept="application/vnd.github.raw",
    )
    if status == 404:
        die(f"source file not found: {SOURCE_REPO}:{source_path}")
    if status != 200 or not isinstance(content, str):
        die(f"could not fetch {SOURCE_REPO}:{source_path} (status {status})")
    return content


def fetch_usernames():
    """Fetch, validate, normalize, deduplicate, and sort source usernames."""
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

    return sorted(usernames)


def fetch_github_keys(username):
    """Fetch one GitHub user's public SSH keys as normalized key/id tuples."""
    quoted = urllib.parse.quote(username, safe="")
    keys = get_paginated(f"/users/{quoted}/keys")
    valid_keys = []

    for entry in keys:
        if not isinstance(entry, dict):
            continue
        key_id = entry.get("id")
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        normalized_key = normalize_public_key(key)
        if not normalized_key:
            log.warning("Skipping invalid public key for %s: id=%s", username, key_id)
            continue
        valid_keys.append((normalized_key, key_id))

    return sorted(valid_keys, key=lambda item: (item[0], str(item[1])))


def normalize_public_key(key):
    """Return a canonical ``type data`` public key string, or ``""`` if invalid."""
    if "\n" in key or "\r" in key:
        return ""
    parts = key.split()
    if len(parts) < 2:
        return ""
    if parts[0] not in ALLOWED_KEY_TYPES:
        return ""
    if not KEY_DATA_RE.match(parts[1]):
        return ""
    return f"{parts[0]} {parts[1]}"


def build_authorized_keys(usernames):
    """Build complete managed authorized_keys content from GitHub usernames.

    Returns ``(content, key_count, users_without_keys)``. Duplicate public keys
    are skipped so one key cannot appear multiple times with different comments.
    The function aborts if the generated key count is below ``MIN_KEYS_REQUIRED``.
    """
    lines = [
        "# This file is managed by dashboard-ssh-key-sync.py.",
        f"# Source: {SOURCE_REPO}:{SOURCE_PATH}",
        "# Manual edits will be overwritten.",
        "",
    ]

    key_count = 0
    users_without_keys = []
    seen_keys = set()

    static_lines = []
    for raw_key in STATIC_AUTHORIZED_KEYS:
        normalized_key = normalize_public_key(raw_key)
        if not normalized_key:
            die(f"invalid static key in STATIC_AUTHORIZED_KEYS: {raw_key!r}")
        if normalized_key in seen_keys:
            log.warning("Skipping duplicate static key: %r", raw_key)
            continue
        seen_keys.add(normalized_key)
        comment = raw_key.split(None, 2)[2].strip() if len(raw_key.split(None, 2)) > 2 else ""
        line = f"{normalized_key} {comment}".rstrip()
        static_lines.append(line)
        key_count += 1

    if static_lines:
        lines.append("# Static keys (STATIC_AUTHORIZED_KEYS)")
        lines.extend(static_lines)
        lines.append("")

    for username in usernames:
        keys = fetch_github_keys(username)
        if not keys:
            users_without_keys.append(username)
            continue
        lines.append(f"# GitHub user: {username}")
        for key, key_id in keys:
            if key in seen_keys:
                log.warning("Skipping duplicate key for %s: id=%s", username, key_id)
                continue
            seen_keys.add(key)
            comment = f"github:{username}"
            if key_id is not None:
                comment += f" github-key-id:{key_id}"
            lines.append(f"{key} {comment}")
            key_count += 1
        lines.append("")

    if users_without_keys:
        log.warning("Users without public GitHub SSH keys: %s", ", ".join(users_without_keys))
    if key_count < MIN_KEYS_REQUIRED:
        die(f"refusing to write authorized_keys: generated only {key_count} key(s)")

    return "\n".join(lines).rstrip() + "\n", key_count, users_without_keys


def target_user_info():
    """Return the passwd entry for ``TARGET_USER`` or abort if it is missing."""
    try:
        return pwd.getpwnam(TARGET_USER)
    except KeyError:
        die(f"local user does not exist: {TARGET_USER}")


def authorized_keys_path(user_info):
    """Return the target authorized_keys path for a passwd entry."""
    return Path(user_info.pw_dir) / ".ssh" / "authorized_keys"


def ensure_can_write(user_info):
    """Abort unless the current process can safely write the target user's keys."""
    if os.geteuid() == 0:
        return
    if os.geteuid() == user_info.pw_uid:
        return
    die(f"must run as root or as local user {TARGET_USER!r} to update authorized_keys")


def write_authorized_keys(content, dry_run):
    """Replace the target authorized_keys file atomically.

    Returns ``True`` only when a real write occurred. Dry-runs and no-op updates
    return ``False``. Before replacement, existing content is backed up next to
    the target file.
    """
    user_info = target_user_info()
    ensure_can_write(user_info)

    auth_path = authorized_keys_path(user_info)
    ssh_dir = auth_path.parent
    old_content = ""
    if auth_path.exists():
        old_content = auth_path.read_text(encoding="utf-8", errors="replace")

    if dry_run:
        action = "would update" if old_content != content else "already up to date"
        log.info("DRY-RUN: %s %s", action, auth_path)
        return False

    if old_content == content:
        log.info("%s is already up to date", auth_path)
        return False

    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(ssh_dir, user_info.pw_uid, user_info.pw_gid)
    os.chmod(ssh_dir, 0o700)

    if auth_path.exists():
        # TODO: Backups accumulate unbounded. Every content change writes
        # authorized_keys.backup.<UTC ts> and nothing prunes them. Over time
        # ~/.ssh fills with backups. Consider keeping the last N.
        backup_path = auth_path.with_name(
            "authorized_keys.backup." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        )
        shutil.copy2(auth_path, backup_path)
        os.chown(backup_path, user_info.pw_uid, user_info.pw_gid)
        os.chmod(backup_path, 0o600)
        log.info("Backed up existing authorized_keys to %s", backup_path)

    fd, tmp_name = tempfile.mkstemp(prefix=".authorized_keys.", dir=str(ssh_dir), text=True)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chown(tmp_path, user_info.pw_uid, user_info.pw_gid)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, auth_path)
        sync_directory(ssh_dir)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    log.info("Updated %s", auth_path)
    return True


def sync_postgres_passwords(usernames, dry_run):
    """Synchronize Postgres role passwords from ``POSTGRES_USERS_PATH`` metadata.

    The users file controls which GitHub users get Postgres credentials and when
    each credential rotates. Passwords are generated locally, stored in a
    root-only JSON state file, applied through ``psql``, and emailed only when
    newly created or rotated.
    """
    if not cfg("POSTGRES_SYNC_ENABLED", POSTGRES_SYNC_ENABLED):
        return

    allowed_users = set(usernames)
    user_entries = fetch_postgres_users(allowed_users)
    state = read_postgres_state()
    next_state, changed_entries, removed_entries = build_postgres_state(state, user_entries)

    log.info("Postgres users: %d", len(user_entries))
    log.info("Postgres password changes: %d", len(changed_entries))
    log.info("Postgres removed managed users: %d", len(removed_entries))

    if dry_run:
        for entry in changed_entries:
            log.info(
                "DRY-RUN: would generate/rotate Postgres password for %s (%s)",
                entry["username"],
                entry["role"],
            )
        for entry in removed_entries:
            log.info(
                "DRY-RUN: would disable removed Postgres role for %s (%s)",
                entry["username"],
                entry["role"],
            )
        return

    if changed_entries and not cfg_list("POSTGRES_EMAIL_TO", POSTGRES_EMAIL_TO):
        die("POSTGRES_EMAIL_TO must be configured before applying password changes")

    for entry in next_state["users"].values():
        apply_postgres_password(entry["role"], entry["password"])
    if POSTGRES_DISABLE_REMOVED_ROLES:
        for entry in removed_entries:
            disable_postgres_role(entry["role"])

    if changed_entries:
        if not send_postgres_password_email(changed_entries):
            die("Postgres passwords were applied, but notification email failed")

    write_postgres_state(next_state)


def fetch_postgres_users(allowed_users):
    """Fetch and parse Postgres users metadata, validating users against the team."""
    content = fetch_source_file(POSTGRES_USERS_PATH)
    entries = {}
    invalid = []

    for line_no, line in enumerate(content.splitlines(), start=1):
        entry = parse_postgres_user_line(line, line_no)
        if entry is None:
            continue
        username = entry["username"]
        if username not in allowed_users:
            invalid.append(f"line {line_no}: {username!r} is not in {SOURCE_PATH}")
            continue
        if username in entries:
            invalid.append(f"line {line_no}: duplicate user {username!r}")
            continue
        entries[username] = entry

    if invalid:
        die("invalid Postgres users: " + "; ".join(invalid))
    if not entries:
        die(f"refusing to run: {SOURCE_REPO}:{POSTGRES_USERS_PATH} contains no users")

    return [entries[name] for name in sorted(entries)]


def parse_postgres_user_line(line, line_no):
    """Parse one postgres_users line into user/role/password_version metadata."""
    text = line.split("#", 1)[0].strip()
    if not text:
        return None

    parts = text.split()
    username = parts[0].lower()
    if not USERNAME_RE.match(username):
        die(f"invalid GitHub username in {POSTGRES_USERS_PATH} line {line_no}: {parts[0]!r}")

    role = default_postgres_role(username)
    password_version = "1"
    positional = []

    for token in parts[1:]:
        if "=" not in token:
            positional.append(token)
            continue
        key, value = token.split("=", 1)
        if key in ("role", "pg_role"):
            role = value
        elif key in ("password_version", "version"):
            password_version = value
        else:
            die(f"unknown key in {POSTGRES_USERS_PATH} line {line_no}: {key!r}")

    if positional:
        role = positional[0]
    if len(positional) > 1:
        password_version = positional[1]
    if len(positional) > 2:
        die(f"too many positional fields in {POSTGRES_USERS_PATH} line {line_no}")

    if not POSTGRES_ROLE_RE.match(role):
        die(f"invalid Postgres role in {POSTGRES_USERS_PATH} line {line_no}: {role!r}")
    if not POSTGRES_VERSION_RE.match(password_version):
        die(
            f"invalid password_version in {POSTGRES_USERS_PATH} line {line_no}: "
            f"{password_version!r}"
        )

    return {
        "username": username,
        "role": role,
        "password_version": password_version,
    }


def default_postgres_role(username):
    """Return the default Postgres role for a GitHub username."""
    return POSTGRES_ROLE_PREFIX + username.replace("-", "_")


def read_postgres_state():
    """Read the local Postgres password state file, or return an empty state."""
    path = Path(POSTGRES_STATE_PATH)
    if not path.exists():
        return {"users": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die(f"could not read {path}: {exc}")
    if not isinstance(state, dict) or not isinstance(state.get("users"), dict):
        die(f"invalid Postgres state file: {path}")
    return state


def build_postgres_state(state, user_entries):
    """Return updated state plus changed and removed managed entries."""
    current_users = state.get("users", {})
    next_users = {}
    changed = []

    for entry in user_entries:
        username = entry["username"]
        existing = current_users.get(username)
        if needs_new_postgres_password(existing, entry):
            password = generate_postgres_password()
            changed.append({
                "username": username,
                "role": entry["role"],
                "password_version": entry["password_version"],
                "password": password,
            })
        else:
            password = existing["password"]

        next_users[username] = {
            "username": username,
            "role": entry["role"],
            "password_version": entry["password_version"],
            "password": password,
        }

    removed = []
    for username, entry in current_users.items():
        if username in next_users:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("role"), str):
            removed.append({
                "username": username,
                "role": entry["role"],
            })

    return {"users": next_users}, changed, sorted(removed, key=lambda item: item["username"])


def needs_new_postgres_password(existing, user_entry):
    """Return whether a postgres_users entry requires a new generated password."""
    if not isinstance(existing, dict):
        return True
    if existing.get("role") != user_entry["role"]:
        return True
    if existing.get("password_version") != user_entry["password_version"]:
        return True
    return not isinstance(existing.get("password"), str) or not existing["password"]


def generate_postgres_password():
    """Generate a high-entropy Postgres password suitable for emailing once."""
    return secrets.token_urlsafe(POSTGRES_PASSWORD_BYTES)


def apply_postgres_password(role, password):
    """Create/update one Postgres role password through psql stdin."""
    if POSTGRES_CREATE_ROLES and not postgres_role_exists(role):
        run_psql(f"CREATE ROLE {quote_pg_identifier(role)} {POSTGRES_ROLE_OPTIONS};")
        log.info("Created Postgres role %s", role)
    run_psql(
        f"ALTER ROLE {quote_pg_identifier(role)} WITH LOGIN PASSWORD "
        f"{quote_pg_literal(password)};"
    )
    log.info("Updated Postgres password for role %s", role)


def disable_postgres_role(role):
    """Disable login for a managed Postgres role removed from the users file."""
    if not POSTGRES_ROLE_RE.match(role):
        log.warning("Skipping invalid removed Postgres role %r", role)
        return
    if not postgres_role_exists(role):
        log.info("Postgres role %s no longer exists", role)
        return
    run_psql(f"ALTER ROLE {quote_pg_identifier(role)} NOLOGIN;")
    log.info("Disabled Postgres login for removed role %s", role)


def postgres_role_exists(role):
    """Return whether a Postgres role already exists."""
    sql = (
        "SELECT 1 FROM pg_catalog.pg_roles "
        f"WHERE rolname = {quote_pg_literal(role)};"
    )
    return run_psql(sql, capture=True).strip() == "1"


def quote_pg_identifier(identifier):
    """Quote a PostgreSQL identifier."""
    if not POSTGRES_ROLE_RE.match(identifier):
        die(f"invalid Postgres identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def quote_pg_literal(value):
    """Quote a PostgreSQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def run_psql(sql, capture=False):
    """Run psql with SQL on stdin, never placing passwords in argv."""
    cmd = [cfg("POSTGRES_PSQL", POSTGRES_PSQL), "-v", "ON_ERROR_STOP=1", "-q", "-w"]
    host = cfg("POSTGRES_HOST", POSTGRES_HOST)
    port = cfg("POSTGRES_PORT", POSTGRES_PORT)
    user = cfg("POSTGRES_USER", POSTGRES_USER)
    database = cfg("POSTGRES_DATABASE", POSTGRES_DATABASE)
    if host:
        cmd.extend(["-h", host])
    if port:
        cmd.extend(["-p", str(port)])
    if user:
        cmd.extend(["-U", user])
    if database:
        cmd.extend(["-d", database])
    if capture:
        cmd.extend(["-t", "-A"])

    env = os.environ.copy()
    postgres_password = os.environ.get(POSTGRES_PASSWORD_ENV)
    if postgres_password:
        env["PGPASSWORD"] = postgres_password

    try:
        result = subprocess.run(
            cmd,
            input=sql,
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}")
    except subprocess.CalledProcessError as exc:
        die(f"psql failed with exit code {exc.returncode}")
    return result.stdout if capture else ""


def write_postgres_state(state):
    """Write the Postgres password state file atomically as root-only data."""
    path = Path(POSTGRES_STATE_PATH)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)

    fd, tmp_name = tempfile.mkstemp(prefix=".postgres-passwords.", dir=str(path.parent), text=True)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        sync_directory(path.parent)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def send_postgres_password_email(changed_entries):
    """Email generated/rotated Postgres passwords to configured recipients."""
    recipients = cfg_list("POSTGRES_EMAIL_TO", POSTGRES_EMAIL_TO)
    smtp_host = cfg("POSTGRES_SMTP_HOST", POSTGRES_SMTP_HOST)
    smtp_port = int(cfg("POSTGRES_SMTP_PORT", POSTGRES_SMTP_PORT))
    use_tls = str(cfg("POSTGRES_SMTP_USE_TLS", POSTGRES_SMTP_USE_TLS)).lower() in (
        "1", "true", "yes", "on")
    message = EmailMessage()
    message["From"] = cfg("POSTGRES_EMAIL_FROM", POSTGRES_EMAIL_FROM)
    message["To"] = ", ".join(recipients)
    message["Subject"] = "Dashboard Postgres passwords updated"
    message.set_content(render_postgres_password_email(changed_entries))

    try:
        if use_tls:
            with smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                timeout=30,
            ) as smtp:
                smtp_login_if_configured(smtp)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(
                smtp_host,
                smtp_port,
                timeout=30,
            ) as smtp:
                smtp_login_if_configured(smtp)
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("could not send Postgres password email: %s", exc)
        return False

    log.info("Sent Postgres password email to %s", ", ".join(recipients))
    return True


def smtp_login_if_configured(smtp):
    """Authenticate to SMTP only when username/password config is present."""
    username = cfg("POSTGRES_SMTP_USERNAME", POSTGRES_SMTP_USERNAME)
    if not username:
        return
    password = os.environ.get(POSTGRES_SMTP_PASSWORD_ENV)
    if not password:
        die(f"{POSTGRES_SMTP_PASSWORD_ENV} is required for SMTP authentication")
    smtp.login(username, password)


def render_postgres_password_email(changed_entries):
    """Render a plaintext email body containing only changed passwords."""
    host = socket.getfqdn() or socket.gethostname()
    lines = [
        f"Postgres passwords were updated on {host}.",
        f"Source: {SOURCE_REPO}:{POSTGRES_USERS_PATH}",
        "",
        "Only newly created or rotated passwords are listed below.",
        "",
    ]
    for entry in changed_entries:
        lines.extend([
            f"GitHub user: {entry['username']}",
            f"Postgres role: {entry['role']}",
            f"Password version: {entry['password_version']}",
            f"Password: {entry['password']}",
            "",
        ])
    return "\n".join(lines)


def send_discord_update(usernames, key_count, users_without_keys):
    """Send an optional Discord notification after a successful key update."""
    webhook_url = cfg("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL).strip()
    if not webhook_url:
        return
    if not is_allowed_discord_webhook(webhook_url):
        log.warning("Discord webhook URL is not a supported HTTPS Discord webhook")
        return

    host = socket.getfqdn() or socket.gethostname()
    shown_users = ", ".join(usernames[:30])
    if len(usernames) > 30:
        shown_users += f", ... and {len(usernames) - 30} more"

    content = (
        f"SSH authorized_keys updated on `{host}` for local user `{TARGET_USER}`.\n"
        f"Source: `{SOURCE_REPO}:{SOURCE_PATH}`\n"
        f"Users: {len(usernames)}; keys: {key_count}\n"
        f"GitHub users: {shown_users}"
    )
    if users_without_keys:
        missing = ", ".join(users_without_keys[:20])
        if len(users_without_keys) > 20:
            missing += f", ... and {len(users_without_keys) - 20} more"
        content += f"\nUsers without public SSH keys: {missing}"

    payload = {
        "username": DISCORD_USERNAME,
        "content": content[:2000],
    }
    post_json(webhook_url, payload)


def is_allowed_discord_webhook(webhook_url):
    """Validate that a webhook URL targets Discord over HTTPS."""
    parsed = urllib.parse.urlparse(webhook_url)
    allowed_hosts = {
        "discord.com",
        "www.discord.com",
        "canary.discord.com",
        "ptb.discord.com",
        "discordapp.com",
        "www.discordapp.com",
    }
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() in allowed_hosts
        and parsed.path.startswith("/api/webhooks/")
    )


def post_json(url, payload):
    """POST a JSON payload with retries for transient webhook failures."""
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if 200 <= resp.status < 300:
                    log.info("Sent Discord notification")
                    return
                log.warning("Discord webhook returned HTTP %s", resp.status)
                return
        except urllib.error.HTTPError as exc:
            retryable = exc.code >= 500 or exc.code == 429
            if retryable and attempt < MAX_RETRIES - 1:
                raw = exc.read().decode("utf-8", "replace")
                wait = discord_retry_after(exc.headers, raw, attempt)
                log.warning("Discord webhook returned %s, retrying in %ss", exc.code, wait)
                time.sleep(wait)
                continue
            log.warning("Discord webhook failed with HTTP %s", exc.code)
            return
        except urllib.error.URLError as exc:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                log.warning("Discord webhook failed (%s), retrying in %ss", exc, wait)
                time.sleep(wait)
                continue
            log.warning("Discord webhook failed: %s", exc)
            return


def discord_retry_after(headers, raw_body, attempt):
    """Return Discord retry delay from headers/body, falling back to backoff."""
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return max(1, int(float(retry_after)))
        except ValueError:
            pass
    try:
        data = json.loads(raw_body)
    except ValueError:
        return 2 ** attempt
    value = data.get("retry_after") if isinstance(data, dict) else None
    if isinstance(value, (int, float)):
        return max(1, int(value))
    return 2 ** attempt


def sync_directory(path):
    """Best-effort fsync of a directory after atomic file replacement."""
    try:
        fd = os.open(path, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install_systemd():
    """Install this script and enable its systemd service/timer."""
    if os.geteuid() != 0:
        die("--install must be run as root")

    user_info = target_user_info()
    install_path = Path(INSTALL_PATH)
    install_path.parent.mkdir(parents=True, exist_ok=True)

    source_path = Path(__file__).resolve()
    if source_path != install_path:
        shutil.copy2(source_path, install_path)
    os.chown(install_path, 0, 0)
    os.chmod(install_path, 0o755)

    service_path = Path("/etc/systemd/system") / SYSTEMD_SERVICE_NAME
    timer_path = Path("/etc/systemd/system") / SYSTEMD_TIMER_NAME
    ssh_dir = authorized_keys_path(user_info).parent
    postgres_state_dir = Path(POSTGRES_STATE_PATH).parent
    postgres_state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(postgres_state_dir, 0o700)

    service_path.write_text(
        render_service(install_path, ssh_dir, postgres_state_dir),
        encoding="utf-8",
    )
    timer_path.write_text(render_timer(), encoding="utf-8")
    os.chmod(service_path, 0o644)
    os.chmod(timer_path, 0o644)

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", SYSTEMD_TIMER_NAME])

    log.info("Installed %s", install_path)
    log.info("Installed and enabled %s", SYSTEMD_TIMER_NAME)
    log.info("Run a one-shot sync with: systemctl start %s", SYSTEMD_SERVICE_NAME)


def render_service(script_path, ssh_dir, postgres_state_dir):
    """Render the systemd service unit used by ``--install``."""
    read_write_paths = f"{ssh_dir} {postgres_state_dir}"
    return f"""[Unit]
Description=Sync {TARGET_USER} SSH authorized_keys from GitHub users
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=root
Group=root
ExecStart=/usr/bin/python3 {script_path} --apply
Nice=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths={read_write_paths}
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LockPersonality=true
MemoryDenyWriteExecute=true
PrivateDevices=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictRealtime=true
SystemCallArchitectures=native
"""


def render_timer():
    """Render the systemd timer unit used by ``--install``."""
    return f"""[Unit]
Description=Run {SYSTEMD_SERVICE_NAME} periodically

[Timer]
OnBootSec={SYSTEMD_ON_BOOT}
OnUnitActiveSec={SYSTEMD_INTERVAL}
RandomizedDelaySec={SYSTEMD_RANDOM_DELAY}
Persistent=true
Unit={SYSTEMD_SERVICE_NAME}

[Install]
WantedBy=timers.target
"""


def run(argv):
    """Run a subprocess command and abort on failure."""
    log.info("Running: %s", " ".join(argv))
    try:
        subprocess.run(argv, check=True)
    except FileNotFoundError:
        die(f"command not found: {argv[0]}")
    except subprocess.CalledProcessError as exc:
        die(f"command failed with exit code {exc.returncode}: {' '.join(argv)}")


def parse_args():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description="Sync a local authorized_keys file from GitHub users."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite authorized_keys; default is dry-run",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="install this script and enable a systemd timer",
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

    if args.install:
        install_systemd()
        return

    usernames = fetch_usernames()
    content, key_count, users_without_keys = build_authorized_keys(usernames)

    log.info("Source users: %d", len(usernames))
    log.info("Generated SSH keys: %d", key_count)
    if users_without_keys:
        log.info("Users without keys: %d", len(users_without_keys))

    changed = write_authorized_keys(content, dry_run=not args.apply)
    if changed:
        send_discord_update(usernames, key_count, users_without_keys)
    sync_postgres_passwords(usernames, dry_run=not args.apply)


if __name__ == "__main__":
    main()
