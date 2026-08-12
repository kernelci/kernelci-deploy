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
