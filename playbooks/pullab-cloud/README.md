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

`aws-infra.yml` needs the AWS collections; `site.yml` does not:

```sh
ansible-galaxy collection install -r requirements.yml
```

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
What each job inherits from the file is the shape: 40 GB root, 3600s max
runtime, `c5a.4xlarge` on amd64 and `c7g.4xlarge` on arm64, both on the latest
AL2023 AMI resolved via SSM.

**How kernel artifacts actually arrive** — not over S3. Every test type
(`baseline`, `ltp`, `unixbench`, and anything unrecognised) maps to the
`url-kernel-boot` vm-test, which receives `artifacts.kernel`,
`artifacts.modules` and `artifacts.rootfs` from the job definition as
`KERNEL_URL` / `MODULES_URL` / `ROOTFS_URL` and `curl`s them **inside the VM**.
Those URLs point at the KernelCI storage server (the Azure-backed service in
`kernelci/kernelci-storage`, behind storage.kernelci.org) — nothing to do with
AWS.

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
