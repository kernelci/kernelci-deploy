# Various sysadmin files for KernelCI project

## Deprecation Notice
Many files and directories in this repository are deprecated and will be removed in 1 month if no objections are raised. These files are not used anymore and are kept only for reference. Please check their respective sections for more details.
Date when they will be (likely)removed: 2025-08-23

## Root directory
Various files in root directory:
- ansible: Deprecated Ansible script from legacy KernelCI project, not used anymore. DEPRECATED: Will be removed in 1 month if no objections.
- chromeos.kernelci.org: Deprecated ChromeOS staging environment, not used anymore. DEPRECATED: Will be removed in 1 month if no objections.
- job.py: Deprecated Python script to run/control KernelCI jobs in legacy/jenkins, not used anymore. DEPRECATED: Will be removed in 1 month if no objections.
- kernel.py: Python script to update kernel mirror in KernelCI project.
- kernelci.org: Deprecated legacy production script, not used anymore. DEPRECATED: Will be removed in 1 month if no objections.
- pending.py: Script to handle pending PR and merge them into staging environment. Likely deprecated.
- staging.kernelci.org: Script to run staging environment for KernelCI project. Updated to use new workflows, mostly initiate github actions workflows, and then update local docker images
- update.py: Old script for staging environment, not used anymore. DEPRECATED: Will be removed in 1 month if no objections.

## data/staging.ini
This file contains permit-list for users allowed to access the staging environment (their PRs are automatically deployed).

## k8s/*
Mostly obsolete old recipes for legacy KernelCI Kubernetes cluster (builders)
DEPRECATED: Will be removed in 1 month if no objections.

## kernelci/*
Probably part of legacy scripts, some library.
DEPRECATED: Will be removed in 1 month if no objections.

## kubernetes/*
Various Kubernetes manifests and scripts for KernelCI project.
Check README.md in this directory for more details.

### deploy.cfg
This file contains the deployment configuration for the KernelCI project.
Essential part of api-pipeline-deploy.sh script.

### api-pipeline-deploy.sh
This script is used to deploy the KernelCI API and Pipeline services to a Kubernetes cluster.
It sets up the necessary namespaces, configure IP, DNS name, and other parameters for the services.
It might do complete deployment, or just update the existing deployment (secrets, configmaps, etc.).

### api-production-update.sh
This script is used to update the KernelCI API and Pipeline services in a production environment with updates from the main branch.
It also updates configuration configmap.
This script is intended to be run as part of github actions workflow, but can be run manually as well.

### create_kci_k8s_azure_build.sh
This is initial version (not complete yet) of script to create KernelCI Kubernetes cluster on Azure for builders.

### extract_secret.py
Supplementary script to extract secrets encoded in base64 from Kubernetes cluster.

### caching/*
This directory contains kubernetes manifests for caching services used by KernelCI builders, to reduce load on storage. Right now it is caching only linux-firmware downloads.

## localinstall
This directory contains scripts and configuration files for local installation of KernelCI services. Please check included README.md for more details.

## playbooks/*
This directory contains Ansible playbooks and roles for deploying and managing KernelCI services. Right now we have only complete playbook for production server, incomplete for monitoring server, and some roles for monitoring in `all` directory (node_exporter listening on port 2000)

### playbooks/dashboard
Playbook for the web dashboard host, which runs the production dashboard
(dashboard.kernelci.org, d.kernelci.org) and the staging one
(staging.dashboard.kernelci.org) side by side:

```sh
cd playbooks/dashboard
ansible-playbook -i inventory.yaml main.yml
```

The compose files here are not templated: both stacks run the compose file that
ships in the `kernelci/dashboard` checkout, so the playbook owns the checkouts,
the environment files, the nginx front end and certificate renewal instead.

Roles:
- `common`: base packages, and Docker only when the host does not have it.
- `nginx`: the TLS front end and the anti-abuse rules (robots.txt, the
  user-agent blocklist and the `fake_macos_blocked` geo check). The blocklist
  file itself is treated as data: created if missing, never rewritten, so a run
  cannot wipe entries added by hand.
- `dashboard-production`: prebuilt ghcr.io images. Refresh them with
  `-e dashboard_production_pull=true`.
- `dashboard-staging`: builds its images on the host. Rebuild with
  `-e dashboard_staging_build=true`.
- `uptime-kuma`: the status page, bound to loopback.
- `certbot`: the renewal timer. Run once with `-e certbot_verify_renewal=true`
  to have the play prove renewal still works.

Both checkouts sit on a detached HEAD at a reviewed commit, and the playbook
leaves them there. Set `dashboard_production_version` or
`dashboard_staging_version` to move one deliberately; nothing fast-forwards a
running dashboard as a side effect.

The `.env` files are never written from this repository: they hold the Django
secret key, the database password, the Discord webhook and SMTP credentials.
The play fails with instructions if one is missing rather than inventing it.

Note on certificates: this host renews with the pip certbot in `/usr/local/bin`,
not the Debian package. The package ships only `/etc/cron.d/certbot`, which can
never run here (cron is not installed, `/usr/bin/certbot` does not exist, and
the entry skips itself under systemd), so the `certbot` role installs its own
`certbot-renew` service and timer.

### playbooks/kcidb-production
Playbook for the production KCIDB submission endpoint (db.kernelci.org), run
with:

```sh
cd playbooks/kcidb-production
ansible-playbook -i inventory.yaml main.yml
```

It is not a copy of kcidb-staging, because the host is not built the same way:
there is no caddy in front (kcidb-rest binds 80/443 itself and renews its own
certificate through its built-in ACME client), no dashboard (that runs on a
different host), the images are prebuilt from ghcr.io rather than built
locally, and the database is Azure managed Postgres, so the self-hosted
`db`/`dbinit` compose services stay behind their profile. The stack also keeps
its deployed location in `/home/azureuser/kcidb-ng`, next to ~19G of spool and
archive data, rather than the `/srv` path staging uses.

Unlike the kcidb-staging common role, this playbook does not move sshd to port
22022 and does not rewrite root's `authorized_keys`. This host answers on 22,
which is what the Azure network security group publishes.

Roles:
- `common`: base packages, and Docker only when the host does not already
  have it.
- `kcidb-ng`: data directories, the compose file, and the stack itself. The
  `.env` file is only ever created, never rewritten: it holds the JWT secret,
  the storage token and the database password. Images are not refreshed by
  default; deploy current builds with `-e kcidb_pull=always`.
- `archivarius`: the submissions archiver built from `tools/submissions_archivarius`.
  Install or upgrade it with `-e archivarius_deb=/path/to/*.deb`.
- `dozzle`: the container log viewer, bound to loopback and reached over an
  ssh tunnel.

### playbooks/monitoring
Grafana on the monitoring host (mon.kernelci.org):

```sh
cd playbooks/monitoring
ansible-playbook -i inventory.yaml main.yml
```

The host runs a compose stack of seven services (Grafana, VictoriaMetrics,
vmagent, vmalert, alertmanager, a Kubernetes exporter and a Kubernetes web
view) plus a second uptime-kuma, behind caddy and nginx. This playbook covers
Grafana only, which is where the irreplaceable state was: thirteen dashboards
that existed nowhere but the SQLite database on that one VM.

Those dashboards are now in `roles/grafana/files/dashboards`, extracted from
the live database. Re-extract them after editing in the UI with:

```sh
ssh kernelci@mon.kernelci.org 'sudo python3' < tools/grafana_export.py > dashboards.json
```

By default the role only stages the files on the host and configures
provisioning, changing nothing about the running Grafana; the compose file
does not mount them, so they are a backup on disk. Set
`-e grafana_provision_dashboards=true` to have Grafana actually load them,
which is what a rebuilt host wants. Grafana then owns those UIDs as
provisioned dashboards, so the provider also sets `allowUiUpdates` to keep
them editable in the UI.

The `vmstack` role manages the scrape and alerting configuration:
`prometheus.yml` (the map of everything the project monitors: node exporters on
the KCIDB, dashboard, staging and docs hosts, API and pipeline metrics, the
ingester and Django workers, the Kubernetes exporter and the blackbox probes),
`alerts.yml`, `alertmanager.yml` and the blackbox modules. These are
configuration rather than accumulated state, so the repository is the source of
truth and the files are copied verbatim; each component reloads over HTTP, so
nothing is restarted. The role also reports any stack service that is not
running, which is how a dead exporter gets noticed.

`roles/uptime-kuma/files/monitors.json` is a sanitised export of the Uptime
Kuma monitors on this host, produced by `tools/uptime_kuma_export.py`. Uptime
Kuma has no file provisioning, so it is a record of what is watched rather than
something the playbook restores.

Out of scope on purpose: the compose stack itself and the Kubernetes
credentials in `/srv/monitoring/config` and `/srv/monitoring/k8suser`.

### playbooks/status
Playbook for the status page host (status.kernelci.org):

```sh
cd playbooks/status
ansible-playbook -i inventory.yaml main.yml
```

The host runs a single packaged daemon, `kernelci-status`
(https://github.com/nuclearcat/kernelci-status), and nothing else. It binds 80
and 443 itself and obtains its own Let's Encrypt certificate, the same way
kcidb-rest does, so there is no web server, no reverse proxy and no Docker on
this machine. The playbook keeps it that way: there is no common role
installing a container runtime.

The package is not published anywhere. Build it with `./build_deb.sh` from the
source repository (which needs Docker, so not on this host) and install it
with `-e kernelci_status_deb=./output/kernelci-status_*.deb`; without that the
role only checks that the package is already installed.

`/etc/kernelci-status.toml` is only ever created, never rewritten: it carries
the bootstrap administrator password, which upstream tells you to remove once a
real account exists. Note that the upstream example sets `staging = true` for
ACME, which issues untrusted certificates; the template here defaults it to
false.

What the daemon actually checks - endpoints, incidents, maintenance windows,
notification channels - lives in its SQLite database and is edited through the
admin UI. That is state, not configuration, so this playbook manages the daemon
and leaves the checks alone.

### playbooks/production
Playbook for the production web/storage server (`vm-production-2025`, reachable
as `docs.kernelci.org:22022`), run with:

```sh
cd playbooks/production
ansible-playbook -i inventory.yaml main.yml
```

Roles:
- `webserver`: nginx vhosts for docs, storage, chromeos storage (decommissioned,
  serves HTTP 410), files and the MCP endpoint, plus the shared Let's Encrypt
  certificate. Every vhost is rendered from `templates/vhost.j2` out of the
  `vhosts` variable, so the recipe is the source of truth. Names that do not
  resolve yet are left out of the certificate request instead of failing the run.
- `storage`: the `kernelci-storage` container behind files.kernelci.org. The
  config file is created only if missing, since the live one holds credentials.
- `mcp`: public read-only KernelCI MCP server (`kci-dev mcp`, streamable HTTP)
  in a virtualenv under /srv/kci-mcp, running as the `kci-mcp` system user and
  bound to 127.0.0.1:8000. It is published by nginx. kci-dev's HTTP transport
  has no authentication, so the config deliberately carries no pipeline URL and
  no token: without them kci-dev never registers `retry_job` or
  `trigger_checkout`, and the public endpoint can only read.
- `common`: host bootstrap (packages, /data mount, ssh port). Excluded from the
  default run, it is only meant for provisioning a new host.

Where kci-dev comes from is configurable, in `playbooks/production/group_vars/all.yml`
or on the command line. The default is the released package from PyPI:

```yaml
mcp_source: pypi
mcp_kci_dev_version: ""   # a version here pins the release
```

To run an MCP change that has not been released, point it at a repository and a
ref instead - a fork and a work branch are as valid as upstream:

```
ansible-playbook -i inventory.yaml main.yml --tags mcp \
  -e mcp_source=git \
  -e mcp_git_repo=https://github.com/nuclearcat/kci-dev.git \
  -e mcp_git_ref=mcp-improvements
```

The ref is resolved to a commit with `git ls-remote` before anything is
installed, and the resulting requirement is written to
`/srv/kci-mcp/.installed-source`. That record is what makes the role idempotent
against a moving branch: a run reinstalls when the resolved commit differs from
the recorded one, and does nothing when it does not. It is also what the weekly
update timer rolls back to when a new commit fails its health check, since a
git build carries the same version string across commits and the version alone
cannot tell you what was serving. A ref that is a commit id is treated as a pin
and turns the update timer off, exactly as `mcp_kci_dev_version` does for PyPI.

The MCP endpoint is temporarily served at
`https://storage.chromeos.kernelci.org/mcp`, reusing a decommissioned vhost that
already has a certificate. Once an A record for `mcp.kernelci.org` exists, set
`enabled: true` on that vhost in `roles/webserver/vars/main.yml`, drop
`mcp_endpoint` from the chromeos vhost and re-run the `webserver` role.

## tools/*
This directory contains various tools and scripts used in the KernelCI project.
### azure_blob_cleanup.py
Script to clean up old blobs in Azure Blob Storage, used for KernelCI artifacts.
### azure_files_cleanup.py
Script to clean up old files in Azure File Storage, used for KernelCI artifacts. As we are not using Azure File Storage anymore, this script is going to be removed in the future.
DEPRECATED: Will be removed in 1 month if no objections.
### buildroot_checksum.sh
Script to calculate checksums for Buildroot images used in KernelCI.
### docker_images_cleanup.py
Script to maintain Docker images in Docker hub, to clean up old images.
### firmware-updater.py
Script to update linux-firmware tarball, stored on production storage, used by KernelCI builders.
### kci-dockerwatch.py
Attempt to monitor and log Docker images in KernelCI project. Not working well, it is IMHO not useful.
DEPRECATED: Will be removed in 1 month if no objections.
### kci-k8swatch.py
Same for kubernetes cluster, not working well, not useful.
DEPRECATED: Will be removed in 1 month if no objections.
### legacy_watchdog.py
This is script to monitor legacy services. Not used anymore, as we are not running legacy services.
DEPRECATED: Will be removed in 1 month if no objections.
### managed_identity.sh
Script to manage Azure AD identities for KernelCI VM. So basically you can control Azure K8S cluster without installing credentials, VM by itself is `credential`. Unfortunately only for Azure K8S cluster, not for other cloud providers.
### monitor-containers.py
One more legacy docker monitoring script, not used anymore.
DEPRECATED: Will be removed in 1 month if no objections.
### grafana_export.py
Exports Grafana dashboards from a Grafana SQLite database, read-only, so they
can be kept in git. Dashboards built in the UI live only in `grafana.db`; on the
monitoring host that is inside a Docker volume on a single VM. Writes one file
per dashboard with `-o`, or a single JSON document to stdout.
### uptime_kuma_export.py
Exports Uptime Kuma monitors from its SQLite database without secrets. Fields
are chosen by allowlist rather than denylist: the monitor table has 77 columns
and several carry credentials in names that do not say so, notably
`database_connection_string`, which holds a full `postgres://` URI including the
password. Credential fields that are set are reported by name only, so a restore
knows what still has to be filled in by hand. The notification and user tables,
which hold webhook URLs and password hashes, are never read.
### postgres_team_sync.py
Synchronizes read-only Postgres logins with the dashboard team file
(`kernelci/dashboard:.github/dashboard-team`). Users in the list get a role with
a generated password and read-only access to the public schema; managed roles no
longer listed are dropped. Reserved accounts (`kcidb*`, superusers, `pg_*`, the
connecting role) are never touched. Defaults to a dry run, `--apply` commits.

Connection settings come from the environment, loaded from a `.env` file so it
can share the one the kcidb-ng stack uses:

```sh
./tools/postgres_team_sync.py --env-file /home/azureuser/kcidb-ng/.env
```

It reads `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, optionally `DB_PORT`,
`GITHUB_TOKEN` and `TEAM_SYNC_EMAIL_TO`. Real environment variables override the
file. A legacy `.dbauth` file is still honoured when no `DB_*` values are set.
Previously ran only on the production KCIDB host, outside version control.
### ssh_key_sync.py
Rebuilds one local user's `authorized_keys` from the GitHub public keys of every
user in the same dashboard team file, plus a static key list. Fully manages the
file: it backs up the old content and replaces it atomically, and refuses to
write when it produced suspiciously few keys. Defaults to a dry run, `--apply`
writes, `--install` sets up a systemd service and hourly timer. It can also
rotate Postgres role passwords, which is off by default.

Settings come from the environment or a `.env` file: `GITHUB_TOKEN`,
`DISCORD_WEBHOOK_URL`, `POSTGRES_HOST`/`PORT`/`USER`/`DATABASE`/`PASSWORD`,
`POSTGRES_EMAIL_TO` and `SMTP_PASSWORD`. Nothing secret lives in the script.
Previously ran only on the production KCIDB host, outside version control.
