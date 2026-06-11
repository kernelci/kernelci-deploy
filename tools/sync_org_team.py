#!/usr/bin/env python3
"""
Reconcile a GitHub organization team against a plain-text list of usernames.

The source of truth is a file (default: .github/dashboard-team in
kernelci/dashboard) containing one GitHub username per line; blank lines and
'#' comments are ignored. This script makes the target team's "member" role
match that list exactly:

  * users in the file but not on the team are added -- and if they are not yet
    members of the organization, GitHub sends them an org + team invitation
    automatically (PUT .../memberships handles both cases);
  * "member"-role users on the team but not in the file are removed from the
    team (they are NOT removed from the organization).

Team maintainers, the authenticated token's own account, and any --exclude
names are never removed, so a bad file cannot lock admins out.

Design goals: zero third-party dependencies (Python standard library only) and
safe-by-default (dry-run unless --apply; aborts on an empty/unfetchable file or
when the number of removals exceeds --max-removals).

Authentication: a token is read from the KCIORG_TOKEN environment variable (never a
command-line argument, so it cannot leak via the process list). A fine-grained
PAT scoped to the org with "Members: Read and write" plus "Contents: Read" on
the source repo is sufficient.

Usage:
  # Dry-run (default) -- prints the plan, changes nothing:
  KCIORG_TOKEN=xxx ./sync_org_team.py --org kernelci --team dashboard

  # Apply the changes:
  KCIORG_TOKEN=xxx ./sync_org_team.py --org kernelci --team dashboard --apply
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "kernelci-org-team-sync"

# GitHub username: 1-39 chars, alphanumeric or single (non-leading/trailing)
# hyphens. Used to reject malformed input before any destructive action.
USERNAME_RE = re.compile(r"^[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}$")

MAX_RETRIES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("sync_org_team")


def die(msg):
    log.error(msg)
    sys.exit(1)


def request(method, url, body=None, accept="application/vnd.github+json"):
    """Perform one GitHub API call with retries on 5xx / rate-limiting.

    Returns (status, data, headers). For 2xx, ``data`` is parsed JSON (or raw
    text when a non-JSON Accept type is requested). For 4xx, the HTTPError is
    surfaced as (status, parsed_error_or_text, headers) so callers can treat,
    e.g., 404 as "not present" rather than fatal.
    """
    token = os.environ.get("KCIORG_TOKEN")
    if not token:
        die("KCIORG_TOKEN environment variable is required")

    if not url.startswith("http"):
        url = API_BASE + url

    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    last_err = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8") if resp.length != 0 else ""
                return resp.status, _parse(raw, accept), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            # Retry on server errors and on secondary-rate-limit responses.
            retryable = exc.code >= 500 or exc.code == 429 or (
                exc.code == 403 and "rate limit" in raw.lower()
            )
            if retryable and attempt < MAX_RETRIES - 1:
                wait = _retry_after(exc.headers, attempt)
                log.warning("%s %s -> %s, retrying in %ss", method, url, exc.code, wait)
                time.sleep(wait)
                last_err = exc
                continue
            return exc.code, _parse(raw, accept), dict(exc.headers)
        except urllib.error.URLError as exc:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                log.warning("%s %s failed (%s), retrying in %ss", method, url, exc, wait)
                time.sleep(wait)
                last_err = exc
                continue
            die(f"{method} {url} failed: {exc}")
    die(f"{method} {url} failed after {MAX_RETRIES} attempts: {last_err}")


def _parse(raw, accept):
    if not raw:
        return None
    if "json" in accept:
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _retry_after(resp_headers, attempt):
    retry_after = resp_headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return int(retry_after)
    return 2 ** attempt


def get_paginated(path):
    """Collect all pages of a list endpoint by following the Link header."""
    sep = "&" if "?" in path else "?"
    url = API_BASE + path + f"{sep}per_page=100"
    results = []
    while url:
        status, data, headers = request("GET", url)
        if status != 200:
            die(f"GET {url} returned {status}: {data}")
        if isinstance(data, list):
            results.extend(data)
        url = _next_link(headers.get("Link"))
    return results


def _next_link(link_header):
    if not link_header:
        return None
    for part in link_header.split(","):
        bits = part.split(";")
        if len(bits) < 2:
            continue
        target = bits[0].strip().strip("<>")
        if any(b.strip() == 'rel="next"' for b in bits[1:]):
            return target
    return None


def fetch_file_usernames(source_repo, source_path):
    """Fetch and parse the developers file; return a set of lowercased logins.

    Aborts the run on a missing/unreadable file, on an empty list, or on any
    malformed username -- none of these should ever drive removals.
    """
    status, content, _ = request(
        "GET",
        f"/repos/{source_repo}/contents/{source_path}",
        accept="application/vnd.github.raw",
    )
    if status == 404:
        die(f"source file {source_repo}:{source_path} not found")
    if status != 200 or not isinstance(content, str):
        die(f"could not fetch {source_repo}:{source_path} (status {status})")

    wanted = set()
    invalid = []
    for line in content.splitlines():
        name = line.split("#", 1)[0].strip()
        if not name:
            continue
        if not USERNAME_RE.match(name):
            invalid.append(name)
            continue
        wanted.add(name.lower())

    if invalid:
        die(f"refusing to run: invalid username(s) in file: {', '.join(invalid)}")
    if not wanted:
        die(f"refusing to run: {source_repo}:{source_path} contains no usernames")
    return wanted


def get_team_members(org, team, role):
    members = get_paginated(f"/orgs/{org}/teams/{team}/members?role={role}")
    return {m["login"].lower() for m in members}


def get_pending_invitations(org, team):
    invites = get_paginated(f"/orgs/{org}/teams/{team}/invitations")
    return {i["login"].lower() for i in invites if i.get("login")}


def get_authenticated_login():
    status, data, _ = request("GET", "/user")
    if status == 200 and isinstance(data, dict):
        return (data.get("login") or "").lower()
    return ""


def add_or_invite(org, team, user):
    status, data, _ = request(
        "PUT",
        f"/orgs/{org}/teams/{team}/memberships/{user}",
        body={"role": "member"},
    )
    if status == 200 and isinstance(data, dict):
        state = data.get("state", "unknown")
        log.info(" + %s (%s)", user, "invited" if state == "pending" else "added")
        return True
    log.error(" ! failed to add/invite %s: %s %s", user, status, data)
    return False


def remove(org, team, user):
    status, data, _ = request("DELETE", f"/orgs/{org}/teams/{team}/memberships/{user}")
    if status in (200, 204):
        log.info(" - %s (removed from team)", user)
        return True
    log.error(" ! failed to remove %s: %s %s", user, status, data)
    return False


def write_summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        log.warning("could not write step summary: %s", exc)


def parse_args():
    p = argparse.ArgumentParser(description="Sync a GitHub org team from a username file.")
    p.add_argument("--org", default="kernelci", help="organization login")
    p.add_argument("--team", default="dashboard", help="team slug")
    p.add_argument("--source-repo", default="kernelci/dashboard",
                   help="owner/repo holding the source file")
    p.add_argument("--source-path", default=".github/dashboard-team",
                   help="path to the username file within the source repo")
    p.add_argument("--apply", action="store_true",
                   help="apply changes (default is a dry-run)")
    p.add_argument("--max-removals", type=int, default=10,
                   help="abort if more than this many members would be removed")
    p.add_argument("--exclude", default="",
                   help="comma-separated usernames that are never removed")
    return p.parse_args()


def main():
    args = parse_args()
    exclude = {e.strip().lower() for e in args.exclude.split(",") if e.strip()}

    wanted = fetch_file_usernames(args.source_repo, args.source_path)
    members = get_team_members(args.org, args.team, "member")
    maintainers = get_team_members(args.org, args.team, "maintainer")
    pending = get_pending_invitations(args.org, args.team)
    self_login = get_authenticated_login()

    protected = exclude | maintainers | ({self_login} if self_login else set())

    to_add = sorted(wanted - members - maintainers - pending)
    to_remove = sorted(members - wanted - protected)

    log.info("Team %s/%s: %d members, %d maintainers, %d pending invite(s)",
             args.org, args.team, len(members), len(maintainers), len(pending))
    log.info("File lists %d username(s)", len(wanted))
    log.info("Plan: %d to add/invite, %d to remove, %d protected",
             len(to_add), len(to_remove), len(protected))
    if to_add:
        log.info(" add/invite: %s", ", ".join(to_add))
    if to_remove:
        log.info(" remove: %s", ", ".join(to_remove))

    summary = [
        f"### Team sync: `{args.org}/{args.team}`",
        "",
        f"- Source: `{args.source_repo}:{args.source_path}` ({len(wanted)} listed)",
        f"- Add / invite: {len(to_add)}" + (f" - {', '.join(to_add)}" if to_add else ""),
        f"- Remove: {len(to_remove)}" + (f" - {', '.join(to_remove)}" if to_remove else ""),
        f"- Mode: {'APPLY' if args.apply else 'dry-run'}",
    ]

    # Safety rail: a truncated/garbled file must not trigger mass removals.
    if len(to_remove) > args.max_removals:
        summary.append(f"- **Aborted**: removals ({len(to_remove)}) exceed "
                       f"--max-removals ({args.max_removals})")
        write_summary(summary)
        die(f"removals ({len(to_remove)}) exceed --max-removals "
            f"({args.max_removals}); aborting without changes")

    if not args.apply:
        log.info("DRY-RUN: no changes made (use --apply to apply)")
        write_summary(summary)
        return

    failures = 0
    if to_add:
        log.info("Applying additions/invitations:")
    for user in to_add:
        if not add_or_invite(args.org, args.team, user):
            failures += 1
    if to_remove:
        log.info("Applying removals:")
    for user in to_remove:
        if not remove(args.org, args.team, user):
            failures += 1

    summary.append(f"- Failures: {failures}")
    write_summary(summary)

    if failures:
        die(f"{failures} operation(s) failed")
    log.info("Done: %d added/invited, %d removed", len(to_add), len(to_remove))


if __name__ == "__main__":
    main()
