# kci-k8s-exporter-rs

Prometheus exporter for the capacity and usage of every Kubernetes cluster in
a kubeconfig. It runs as the `k8sexporter` service of the monitoring stack on
mon.kernelci.org (`/srv/monitoring`), scraped by vmagent as job `k8sexporter`,
and feeds the "Kubernetes" Grafana dashboard and the "Kubernetes cluster
unreachable" alert.

It replaces `kci-k8s-metrics`, a Go exporter whose source was lost; it was
rebuilt from the unstripped binary and its live output. Metrics are encoded by
the [`prometheus`](https://crates.io/crates/prometheus) crate, and
`kci_k8s_cluster_status{cluster,namespace,status}` keeps the original's names,
labels and values. See the comment at the top of `src/main.rs` for the metric
reference.

Differences from the original:

- Clusters are polled in the background, in parallel, each under `--timeout`,
  and `/metrics` serves the last result. The original polled them one after
  another during the scrape, so unreachable clusters pushed every scrape past
  vmagent's timeout and nothing was recorded.
- New per-cluster metrics: `kci_k8s_cluster_up`,
  `kci_k8s_cluster_poll_duration_seconds`,
  `kci_k8s_cluster_poll_timestamp_seconds`. An unreachable cluster is still
  reported with every `kci_k8s_cluster_status` value 0, as before.
- GKE contexts (kubeconfig user running `gke-gcloud-auth-plugin`) get their
  access token from `gcloud config config-helper`, run by the exporter
  (`--gcloud` to point at another binary). One token serves all GKE clusters
  and is refreshed five minutes before it expires. The image needs `gcloud`
  and the gcloud credentials, but not the auth plugin.
- Flags use `--double-dash` and durations like `30s`, `1m`; `--listen` takes
  `host:port` (default `0.0.0.0:8080`).

## Test

```sh
cargo test
cargo run --release -- --dryrun --verbose   # polls ~/.kube/config once, prints metrics
```

## Deploy

Copy `Cargo.toml`, `Cargo.lock`, `src/` and `Dockerfile` to
`/srv/monitoring/k8s-exporter/` (replacing the old build context), and in
`/srv/monitoring/docker-compose.yml`:

```yaml
  k8sexporter:
    restart: always
    container_name: k8sexporter
    build: k8s-exporter/
    depends_on:
      - "victoriametrics"
    volumes:
      - ./k8suser/.kube:/root/.kube
      - ./k8suser/.config:/root/.config   # gcloud credentials, for GKE
    networks:
      - vm_net
```

(no `command:` - the entrypoint is the exporter; pass flags as e.g.
`command: ['--timeout', '20s']`). Then:

```sh
cd /srv/monitoring
docker compose build k8sexporter && docker compose up -d k8sexporter
```

The kubeconfig is re-read on every poll, so adding or removing a context does
not need a restart.
