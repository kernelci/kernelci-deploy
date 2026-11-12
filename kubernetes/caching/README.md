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
| `aks-kbuild-medium-1` | Azure westus3 | azurefile (legacy firmware) | yes |
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

### Legacy shared-data + file-sync (linux-firmware)

A read-only RWX volume `shared-data` mounted at `/data` in every build pod
(kernelci-core `config/runtime/base/kubernetes.jinja2`), refreshed by the
`file-sync` CronJob.  Holds `linux-firmware.tar.gz`.

- GKE: `filestore-pvc.yaml` (nfs-server Deployment on a 50Gi disk + NFS PV)
- AKS: `azurefile-pvc.yaml`, `azurefile-cronjob.yaml`
- `croncache.yaml`: standalone GKE refresh CronJob (also embedded in
  `filestore-pvc.yaml`)

All refresh manifests use conditional HTTP requests with the saved ETag. A
changed archive is downloaded to a unique temporary file on the shared volume,
checked with `gzip -t`, and renamed over the published file only on success.
Readers therefore keep the complete old file while a replacement downloads.
Failed downloads leave both the published file and its ETag unchanged. CronJob
overlap is disabled. The GKE schedule remains daily at 00:05 UTC; AKS remains
hourly at minute 20.

`deploy-cacher.sh` now delegates explicit HTTP-cache commands to
`deploy-tarball-cache.sh`; it no longer provisions or recreates NFS volumes.
The legacy manifests remain available for older build images during migration.

### tarball-cache (kernel source and firmware tarballs)

`tarball-cache.yaml`: an nginx pull-through cache (ConfigMap, 100Gi PVC on
`standard-rwo` on GKE / `managed-csi` on AKS, Deployment, ClusterIP Service `tarball-cache`).
kernelci-core kbuild downloads `http://tarball-cache/<name>` when the
Service resolves, and falls back to `https://files.kernelci.org/<name>`
when it does not or the download fails, so the cache is optional per
cluster.

- Only GET/HEAD of `*.tar.gz` / `*.tar.xz` is proxied; everything else is 404.
- `/linux-firmware.tar.gz` is fetched from `storage.kernelci.org` with a separate
  cache key; other tarballs are fetched from `files.kernelci.org`.
- Upstream `Cache-Control` and `Expires` headers determine freshness, with a
  ten-minute fallback. Expired entries are revalidated using ETag/Last-Modified,
  so changes at an existing URL are discovered without redownloading unchanged
  files. Entries unused for seven days are removed (90 GB maximum).
- `proxy_cache_lock` makes concurrent requests share an upstream fetch.
- `X-Cache` response header and the pod log (`cache=HIT|MISS`) show results.

The companion `kernelci-core` firmware downloader fetches
`http://tarball-cache/linux-firmware.tar.gz`, checks the gzip archive, and
renames its local temporary download before extraction. Missing, failed,
interrupted, or corrupt cache responses fall back to a fresh download from
`https://storage.kernelci.org/linux-firmware.tar.gz`. Updated Kubernetes job
templates no longer mount the `shared-data` PVC.

### Firmware migration order

1. Render and apply the HTTP-cache manifest to each build cluster, including
   both AKS clusters. The deployment script injects a manifest checksum into
   the Pod template, so nginx rolls automatically when its configuration changes.
2. Update every build image with the companion `kernelci-core` downloader
   first, while the scheduler still mounts `/data`. New clients use HTTP and
   tolerate the extra mount; older images still need it. Verify a firmware-using
   build and nginx `cache=MISS` then `cache=HIT` for `/linux-firmware.tar.gz`.
3. Only after all build images use the HTTP downloader, update the scheduler's
   `kernelci-core` runtime template to remove the PVC mount from new Jobs.
   Verify a new build succeeds without `/data`. Updating the scheduler first
   would break old build images. The direct HTTP fallback also lets new images
   work when a cluster cache is unavailable.
4. After the new images and scheduler are active, suspend the old `file-sync`
   CronJob. Wait for all older running/pending Jobs that reference `shared-data`
   to finish before retiring their NFS deployment/service or shared PVCs.
   HTTP-cache deployment never deletes these resources; retain them for rollback
   until the migration is verified. For rollback to older build images, restore
   the scheduler mount template before switching those images back.

For example, rendering is offline; check and diff are read-only:

```sh
./deploy-tarball-cache.sh render aks-kbuild-medium-1
./deploy-tarball-cache.sh check <context>
./deploy-tarball-cache.sh diff <context>
./deploy-tarball-cache.sh apply <context>
```

These repository changes do not by themselves migrate running build images or
retire any existing storage.

Local validation, using a small HTTP fixture and nginx rather than production:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
KCI_TEST_NGINX=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The second command needs Docker and `nginx:1.28-alpine`. Tests check cluster
rendering and automatic nginx rollout on configuration changes, plus cache hits,
conditional revalidation, changed content at the same URL, and concurrent
downloads. Publication tests check corrupt/interrupted downloads and readers
during atomic replacement.

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
