# pullab_cloud — KernelCI pull-labs on AWS

Ansible for [`kernelci/pullab_cloud`](https://github.com/kernelci/pullab_cloud):
the poller that consumes `pull-labs-aws-ec2` jobs from the KernelCI API, runs
them on EC2 VMs, and reports results back.

This codifies a deployment that was set up by hand. It was written from the
live state of `kci-aws` as of 2026-08-31; the "As deployed today" section below
records what was actually found, including the parts that are not good.

## Layout

| Path | Purpose |
| --- | --- |
| `site.yml` | Provision the poller host over SSH |
| `aws-infra.yml` | Create/verify AWS resources (runs locally against the API) |
| `group_vars/all.yml` | Every resource name, region and tuning knob |
| `group_vars/vault.yml.example` | Template for the secrets, to be vaulted |
| `roles/pullab_cloud/` | Packages, checkout, config, systemd units |
| `roles/pullab_aws_infra/` | S3 / ECR / ECS / CloudWatch bootstrap |
| `requirements.yml` | Galaxy collections needed by `aws-infra.yml` |

## Usage

```sh
cp group_vars/vault.yml.example group_vars/vault.yml
$EDITOR group_vars/vault.yml
ansible-vault encrypt group_vars/vault.yml

# always dry-run first
ansible-playbook site.yml --check --diff --ask-vault-pass
ansible-playbook site.yml --ask-vault-pass

# AWS resources: no-op unless explicitly enabled
ansible-playbook aws-infra.yml --check --diff
ansible-playbook aws-infra.yml -e pullab_infra_apply=true
```

To deploy new code, add `-e pullab_update_checkout=true` — otherwise the
checkout is left alone so a routine run never pulls `main` from under a
running poll cycle.

Each entry in `pullab_pollers` can override `api_base_uri`,
`kcidb_submit_url`, and `token_variable` (the name of its secret variable).
Give production and staging entries
distinct names, such as `prod-amd64` and `staging-amd64`. Configuration is
written to `examples/aws/config-<name>.json`, credentials to a root-only
`/etc/pullab_cloud/<name>.env`, and polling cursors to
`/var/lib/pullab_cloud/<name>.json`.

All instances share an execution lock, so pipeline runs do not race over
the shared AWS resources or build images concurrently on the poller host.
Existing IAM roles are preserved by default. Set `pullab_image_tag` to a
revision-specific tag when upgrading so the ECS image is built from the
matching checkout once and reused on subsequent runs.

When migrating, copy the existing production cursors before starting the
new timers. Bootstrap staging cursors far enough back to include its
currently available jobs. Confirm no legacy poll cycle is running before
stopping the screen loop and changing the installed Python package.

The current upstream `url-kernel-boot` test remains a download scaffold:
without a boot hook it emits a successful placeholder result on the AMI
kernel. Updating this deployment does not implement kernel boot or LTP
execution. Treat those reported passes accordingly.

`aws-infra.yml` needs the AWS collections; `site.yml` does not:

```sh
ansible-galaxy collection install -r requirements.yml
```

### Public console logs

The results bucket permits anonymous `s3:GetObject` only for
`*/test_*/output/*/console-output.log`, matching the URLs published to KCIDB.
Public ACLs remain blocked. CORS allows `GET` and `HEAD` from
`https://dashboard.kernelci.org`; override `pullab_results_cors_origins` to
add another dashboard origin. Other results and test payloads remain private.

Use `--tags s3` to configure only the results bucket. The apply and expected
AWS account checks still run. To use the credentials already on the poller
host, run from this directory (substitute the expected account ID):

```sh
ansible-playbook aws-infra.yml --tags s3 --check --diff \
  -e pullab_infra_apply=true -e pullab_aws_account_id=EXPECTED_ACCOUNT_ID \
  -e pullab_infra_host=kci-aws -e ansible_connection=ssh \
  -e ansible_become=true -e ansible_python_interpreter=/usr/bin/python3.11
```

Remove `--check --diff` to deploy after reviewing the dry run. Account-level
S3 public-access restrictions, if configured, must also permit bucket policies;
this playbook only manages the bucket-level settings.
Some collection versions skip S3 modules in check mode. In that case, inspect
the current bucket configuration separately before applying, and verify the
public log URL and CORS response afterward.

## As deployed today (2026-08-31)

**Poller host** — `kci-aws`, a `t3.micro` in
**us-east-1**, Amazon Linux 2023, user `ec2-user`. Note the split: the host is
in us-east-1, everything it drives is in **eu-west-2**. That works only because
the pipeline config carries an explicit `region`; `/root/.aws/config` says
`us-east-1`.

**How it runs** — a detached root `screen` session (`929013.pts-1`) running
`/root/pullab_cloud/loop.sh`, alive since early June:

```sh
while true; do
  ./prod-amd64.sh
  ./prod-arm64.sh
  sleep 300
done
```

Each `prod-*.sh` is a one-liner that inlines `UNIFIED_TOKEN=<jwt>` and calls
`pull_labs_poller --config examples/aws/config-<arch>-prod.json --once`.

`site.yml` replaces this with `pullab-poller@amd64` / `pullab-poller@arm64`
oneshot services driven by 300s timers — same cadence, but restarts on reboot,
logs to the journal, and keeps the token out of an executable script.

**Not in git** — `loop.sh`, `prod-amd64.sh`, `prod-arm64.sh`,
`config-amd64-prod.json` and `config-arm64-prod.json` are all untracked in the
checkout on the host, as is a local modification to `config.json`. The two prod
configs are reproduced by `roles/pullab_cloud/templates/config.json.j2`.

**Python** — `python3.11` (AL2023's `python3` is 3.9 and unsupported).
`kernel-ci-cloud-labs` is pip-installed editable into the *system* python3.11,
not into the `.venv` that also exists in the checkout; the scripts additionally
set `PYTHONPATH=src`. Both are reproduced as-is.

### AWS resources (eu-west-2)

| Kind | Name | Notes |
| --- | --- | --- |
| S3 | `kernelci-results` | Results, prefix `results/` |
| S3 | `kernelci-storage` | Named in `external_storage`, but no such bucket exists — see "How kernel artifacts actually arrive" |
| ECR | `kernelci-ecr` | Fargate test image |
| ECS | `kernelci-cluster` | Task family `kernelci-task`, 1024 CPU / 2048 MB |
| IAM | `kernelci-ecs-role` (+ instance profile) | Recreated on every run by the tool |
| Logs | `/ecs/kernelci-task` | 7 day retention |
| Logs | `/ec2/kernelci-vms` | 3 day retention |

A parallel `kernel-ci-exampleuser-*` set (bucket, cluster, ECR repo, IAM role,
log groups) also exists from the default `--prefix kernel-ci-$USER-` in the
upstream quickstart. It is not used by the prod pollers and is left alone here.

**Test matrix** — `test_config.vms` in the configs is a *template, not the
job list*. `pull_labs_translate.py` rewrites `test_config` wholesale for every
job (`"vms": [vm_entry]`), carrying over only `role_name`, so the
`simple-unixbench` / `unixbench-kernel-regression` entries never actually run.
With `arch` set, the native translator uses `kernelci.platform_map` to
inherit the configured AMI, instance type and 40 GB root volume. Test deadlines
come from the job; setup headroom is described below.

**How kernel artifacts arrive** — the job definition supplies HTTP(S)
`artifacts.kernel`, `artifacts.modules` and optional `artifacts.rootfs` URLs.
The VM downloads them as `KERNEL_URL` / `MODULES_URL` / `ROOTFS_URL`.
With the native runner, `boot`/`baseline` map to `url-kernel-boot`, and `ltp`
maps to `url-kernel-ltp`. Unsupported types fail explicitly. Boot tests install
and reboot into the supplied kernel. LTP uses the supplied rootfs's userspace
after that reboot. KernelCI artifact storage is separate from the AWS results
bucket and the inert `external_storage` setting.

This is why the `external_storage` bucket does not exist: the S3-bucket field
was filled in with the *name of the KernelCI storage service*, which is a
different thing that happens to share the name. `external_storage` is only ever
read through `s3.copy_object` / `list_objects_v2`, so it could not reach an HTTP
service even if it were meant to. Across all 39k+ run logs the only test that
ever reaches that code path is `url-kernel-boot` (1604 times), whose
`external_requirements.json` is all-`false` — so the bucket is never contacted.
Leave it alone; do not create an S3 bucket by that name.

## Known problems this does not fix

These are recorded deliberately — each needs a decision, not a playbook.

1. **Root-account access keys.** `/root/.aws/credentials` holds keys for
   `arn:aws:iam::<account-id>:root`. Every VM, bucket and role in the account
   is reachable with them. Replace with a scoped IAM user, or attach an
   instance profile to the poller host and set
   `pullab_manage_aws_credentials: false`.
2. **Plaintext JWT on disk.** The `UNIFIED_TOKEN` in `prod-*.sh` is committed
   to no repo but sits world-readable-by-root in an executable, and is visible
   in `ps` while a poll runs. It expires 2031. It should be rotated once the
   vault-managed environment file is in use.
3. **`external_storage` is inert and misleadingly named.** It points at
   `kernelci-storage`, which is the KernelCI storage *service*, not an S3
   bucket (see above). Nothing reads it today, so this is latent, not broken.
   It only starts to matter if someone wants the kernel-install tests
   (`unixbench-kernel-regression`, `example-kernel-reboot-test`,
   `simple-source-reboot`) — those need real RPMs under `kernel-rpms/binary/`
   in a genuine S3 bucket, which should then be given a name that does not
   collide with the storage service. The IAM policy also grants S3 access to
   `arn:aws:s3:::kernelci-storage*`, which is equally inert.
4. **Per-run log groups never expire.** `/ec2/kernelci-vms/run_*` groups are
   created without retention and there are thousands of them. Retention is only
   set on the two parent groups.
5. **39k+ log directories** under `/root/pullab_cloud/logs/` on a `t3.micro`.
   No rotation.
6. **Single point of failure.** One `t3.micro`, one screen session, no
   monitoring. The systemd units help; alerting does not exist.

## Native kernel and LTP runner

The native runner patch replaces the URL download scaffold with two stages:
installation of the supplied kernel/modules and an SSM reboot, followed by
release/build/hash/boot-ID verification. LTP jobs run the supplied rootfs's Kirk
and requested suites after that verification. Unsupported tests and empty or
failed Kirk reports cannot pass. Deploy a pinned pullab_cloud commit containing
this implementation and a unique `pullab_image_tag`; ECS must register and pin
the matching task definition revision. Older task-family existence checks can
silently keep the previous image.

Each poller may set `arch: x86_64` or `arch: arm64` to pass its configured AMI,
instance type and root volume through `kernelci.platform_map`. Set
`environment: staging` for staging pollers; production is the default.
`pullab_skipfiles` maps requested file names to pinned HTTP(S) artifacts. The
role supplies the LKFT skipfile absent from the current KernelCI LTP rootfs.
Selectors use the configured environment and the job's platform/kernel branch.
Missing requested skipfiles fail explicitly. Native payloads do not use the
inert `external_storage` bucket.

Before switching source, stop timers, drain active services and preserve the
checkout, configurations, cursors, environment files and active ECS revision.
Validate fresh ARM64 boot and x86_64 LTP canaries under the shared execution
lock before switching scheduled pollers. Keep production/staging cursors and
credentials separate. LTP needs its full test timeout plus kernel installation
and reboot time; absent an explicit job deadline, the native translator adds
900 seconds of setup time (with a 3600-second minimum and 43200-second cap).
