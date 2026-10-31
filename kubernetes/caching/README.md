# Build cluster caching

Caches that keep kbuild jobs from downloading the same files from KernelCI
storage over and over.  Egress from the prod VM (files.kernelci.org,
storage.kernelci.org) is billed per GB, and build clusters are its largest
consumer.

## Clusters

Build jobs are scheduled on these clusters (kernelci-pipeline
`config/pipeline.yaml`, runtime `k8s-all`), all in namespace `default`:

| Context | Where | shared-data / file-sync | tarball-cache |
|---|---|---|---|
| `aks-kbuild-medium-1` | Azure westus3 | azurefile | not needed (same region as prod VM, free egress) |
| `aks-kbuild-scus-1` | Azure southcentralus | azurefile (set up by hand, not in this repo's scripts) | yes |
| `gke_android-kernelci-external_europe-west1-d_kci-eu-west1` | GCP | nfs-server | yes |
| `gke_android-kernelci-external_europe-west4-c_kci-eu-west4` | GCP | nfs-server | yes |
| `gke_android-kernelci-external_us-central1-c_kci-us-central1` | GCP | nfs-server | yes |
| `gke_android-kernelci-external_us-east4-c_kci-big-us-east4` | GCP | nfs-server | yes |
| `gke_android-kernelci-external_us-west1-a_kci-us-west1` | GCP | nfs-server | yes |

GKE clusters are only reachable through the `kernelci-staging` jumphost and
with the kernelci gcloud service account:

```sh
ssh -fN -D 127.0.0.1:18080 kernelci-staging
export HTTPS_PROXY=socks5://127.0.0.1:18080
export CLOUDSDK_CONFIG=~/.config/gcloud_kernelci
export KUBECONFIG=~/.kube/gke-kernelci/config   # own directory, see deploy-tarball-cache.sh
```

## Components

### shared-data + file-sync (linux-firmware)

A read-only RWX volume `shared-data` mounted at `/data` in every build pod
(kernelci-core `config/runtime/base/kubernetes.jinja2`), refreshed by the
`file-sync` CronJob.  Holds `linux-firmware.tar.gz`.

- GKE: `filestore-pvc.yaml` (nfs-server Deployment on a 50Gi disk + NFS PV)
- AKS: `azurefile-pvc.yaml`, `azurefile-cronjob.yaml`
- `croncache.yaml`: the original CronJob

> **Warning:** `deploy-cacher.sh` run **without arguments** deletes and
> recreates `shared-data`, `shared-data-pv`, the `filestore-rwx` storage
> class and the `nfs-server` Deployment on every GKE cluster.  Use
> `deploy-cacher.sh update` or `deploy-cacher.sh fetch` for routine work.

### tarball-cache (kernel source tarballs)

`tarball-cache.yaml`: an nginx pull-through cache (ConfigMap, 100Gi PVC on
`standard-rwo` on GKE / the default class on AKS, Deployment, ClusterIP Service `tarball-cache`).
kernelci-core kbuild downloads `http://tarball-cache/<name>` when the
Service resolves, and falls back to `https://files.kernelci.org/<name>`
when it does not or the download fails, so the cache is optional per
cluster.

- Only GET/HEAD of `*.tar.gz` / `*.tar.xz` is proxied; everything else is 404.
- Entries are kept 7 days (90 GB max); `proxy_cache_lock` makes concurrent
  jobs of one checkout share a single upstream fetch.
- `X-Cache` response header and the pod log (`cache=HIT|MISS`) show results.

Deploy with `deploy-tarball-cache.sh`, which is additive only (never
deletes, refuses to touch same-named objects it did not create):

```sh
./deploy-tarball-cache.sh check            # read-only, all clusters: access, clashes, server dry-run
./deploy-tarball-cache.sh diff  <ctx>      # read-only
./deploy-tarball-cache.sh apply <ctx>      # one cluster at a time, or --all
./deploy-tarball-cache.sh status           # objects, pod, HIT/MISS counts
```

Quick test from inside a cluster:

```sh
kubectl run tc-test --rm -it --restart=Never --image=curlimages/curl -- \
  curl -sI http://tarball-cache/<some-tarball>.tar.gz   # 2nd run: X-Cache: HIT
```

Removal (manual, on purpose not scripted) — builds fall back to direct
download as soon as the Service is gone:

```sh
kubectl --context=<ctx> -n default delete service/tarball-cache \
  deployment/tarball-cache configmap/tarball-cache-nginx
kubectl --context=<ctx> -n default delete pvc/tarball-cache   # drops the cached data
```

## Cluster change log

Record every change made to the build clusters here (date, clusters, what,
who).  Newest first.

| Date | Clusters | Change | Status |
|---|---|---|---|
| 2026-10-31 | all 5 GKE | Deployed `tarball-cache` (ConfigMap `tarball-cache-nginx`, PVC/Deployment/Service `tarball-cache`; PVC on `standard-rwo`). kci-big-us-east4 first got a `standard` (pd-standard) PVC that cannot attach to its C3 nodes; that new, empty PVC and its Deployment were deleted and recreated on `standard-rwo`. Verified in each cluster: MISS then HIT. Builds use it once the kernelci-core kbuild change is merged. | applied |
| 2026-10-31 | aks-kbuild-scus-1 | Add `tarball-cache` (same objects, AKS default storage class). Read-only `check` passed. | proposed, not applied |
| (before 2026-10) | all 7 | `shared-data` volume + `file-sync` CronJob for linux-firmware (`deploy-cacher.sh`, `azurefile-*.yaml`); scus set up by hand | in place |
