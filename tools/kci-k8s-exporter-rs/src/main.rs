//! kci-k8s-exporter-rs reports capacity and usage of every Kubernetes cluster
//! in a kubeconfig as Prometheus metrics, for the VictoriaMetrics/Grafana
//! stack on mon.kernelci.org.
//!
//! It replaces kci-k8s-metrics, a Go exporter whose source was lost; it was
//! rebuilt from the unstripped binary and its live output. The output is
//! compatible, so the "Kubernetes" dashboard and the "Kubernetes cluster
//! unreachable" alert keep working:
//!
//! ```text
//! kci_k8s_cluster_status{cluster, namespace, status}
//! ```
//!
//! namespace is "_all_" for the whole cluster, plus one series set for
//! --namespace (default "default") when that namespace exists. status is one
//! of:
//!
//! ```text
//! running, pending, total           pod counts by phase
//! allocated_cpu, allocated_memory   sum of container requests, running pods
//! pending_cpu, pending_memory       sum of container requests, pending pods
//! total_{cpu,memory,storage}        sum of node capacity (whole cluster)
//! allocatable_{cpu,memory,storage}  sum of node allocatable (whole cluster)
//! used_cpu, used_memory             metrics.k8s.io usage of the pods
//! used_storage                      always 0, kept for compatibility
//! ```
//!
//! The *_storage values read the "storage" resource, which nodes do not
//! report, so they are 0 - as they were in the original.
//!
//! Clusters are polled in the background, in parallel, each under a timeout,
//! and /metrics serves the last result. The original polled them one after
//! another inside the HTTP handler, so a few unreachable clusters (30s dial
//! timeout each) pushed every scrape past vmagent's scrape_timeout and
//! nothing at all was recorded - for two weeks in October 2026. A cluster that cannot be read is
//! still reported with every value 0, which the dashboard and the alert rely
//! on, and with kci_k8s_cluster_up 0.
//!
//! GKE contexts (those whose kubeconfig user runs gke-gcloud-auth-plugin) are
//! authenticated with an access token from `gcloud config config-helper`,
//! run by the exporter itself. One token serves every GKE cluster; it is
//! cached and fetched again shortly before it expires.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context as _, Result, anyhow, bail};
use axum::{
    Router, extract::State, http::StatusCode, http::header, response::IntoResponse,
    response::Response, routing::get,
};
use clap::Parser;
use k8s_openapi::api::core::v1::{Namespace, Node, Pod};
use k8s_openapi::apimachinery::pkg::api::resource::Quantity;
use kube::api::{Api, ListParams};
use kube::config::{AuthInfo, KubeConfigOptions, Kubeconfig};
use kube::{Client, Config};
use prometheus::process_collector::ProcessCollector;
use prometheus::{Encoder, GaugeVec, Opts, Registry, TextEncoder};
use serde::Deserialize;
use tokio::sync::{Mutex, RwLock};
use tracing::{info, warn};

const ALL_NAMESPACES: &str = "_all_";
const GKE_PLUGIN: &str = "gke-gcloud-auth-plugin";

#[derive(Parser, Debug)]
#[command(
    version,
    about = "Kubernetes cluster metrics for the KernelCI monitoring stack"
)]
struct Args {
    /// Path to the kubeconfig file [default: $HOME/.kube/config]
    #[arg(long)]
    kubeconfig: Option<PathBuf>,
    /// Namespace reported on its own, besides the whole cluster
    #[arg(long, default_value = "default")]
    namespace: String,
    /// Address to listen on
    #[arg(long, default_value = "0.0.0.0:8080")]
    listen: String,
    /// Path to serve metrics on
    #[arg(long, default_value = "/metrics")]
    path: String,
    /// How often to poll the clusters
    #[arg(long, default_value = "1m", value_parser = humantime::parse_duration)]
    interval: Duration,
    /// Time limit for polling one cluster, gcloud token refresh included
    #[arg(long, default_value = "30s", value_parser = humantime::parse_duration)]
    timeout: Duration,
    /// gcloud binary used to get access tokens for GKE clusters
    #[arg(long, default_value = "gcloud")]
    gcloud: String,
    /// Verbose output
    #[arg(long)]
    verbose: bool,
    /// Poll once, print the metrics to stdout and exit
    #[arg(long)]
    dryrun: bool,
}

/// One series set of kci_k8s_cluster_status.
#[derive(Clone, Copy, Default, Debug)]
struct Stats {
    running: f64,
    pending: f64,
    total: f64,
    allocated_cpu: f64,
    allocated_memory: f64,
    pending_cpu: f64,
    pending_memory: f64,
    total_cpu: f64,
    total_memory: f64,
    allocatable_cpu: f64,
    allocatable_memory: f64,
    total_storage: f64,
    allocatable_storage: f64,
    used_cpu: f64,
    used_memory: f64,
    used_storage: f64,
}

impl Stats {
    fn values(&self) -> [(&'static str, f64); 16] {
        [
            ("running", self.running),
            ("pending", self.pending),
            ("total", self.total),
            ("allocated_cpu", self.allocated_cpu),
            ("allocated_memory", self.allocated_memory),
            ("pending_cpu", self.pending_cpu),
            ("pending_memory", self.pending_memory),
            ("total_cpu", self.total_cpu),
            ("total_memory", self.total_memory),
            ("allocatable_cpu", self.allocatable_cpu),
            ("allocatable_memory", self.allocatable_memory),
            ("total_storage", self.total_storage),
            ("allocatable_storage", self.allocatable_storage),
            ("used_cpu", self.used_cpu),
            ("used_memory", self.used_memory),
            ("used_storage", self.used_storage),
        ]
    }
}

/// Outcome of polling one kubeconfig context.
#[derive(Clone, Debug)]
struct ClusterResult {
    up: bool,
    duration: Duration,
    polled_at: SystemTime,
    namespaces: BTreeMap<String, Stats>,
}

fn zero_stats() -> BTreeMap<String, Stats> {
    BTreeMap::from([(ALL_NAMESPACES.to_string(), Stats::default())])
}

/// Access token for GKE, from gcloud.
struct GcloudToken {
    token: String,
    expiry: SystemTime,
}

struct Exporter {
    args: Args,
    kubeconfig: PathBuf,
    results: RwLock<BTreeMap<String, ClusterResult>>,
    // Held across the gcloud call so parallel GKE polls share one refresh.
    gcloud_token: Mutex<Option<GcloudToken>>,
}

#[derive(Deserialize)]
struct ConfigHelper {
    credential: ConfigHelperCredential,
}

#[derive(Deserialize)]
struct ConfigHelperCredential {
    access_token: String,
    token_expiry: String,
}

impl Exporter {
    /// Returns a valid GKE access token, running gcloud when the cached one
    /// is missing or expires within five minutes. config-helper refreshes
    /// the gcloud credentials itself when they are stale.
    async fn gke_token(&self) -> Result<String> {
        let mut cached = self.gcloud_token.lock().await;
        if let Some(t) = cached.as_ref()
            && t.expiry > SystemTime::now() + Duration::from_secs(300)
        {
            return Ok(t.token.clone());
        }
        if self.args.verbose {
            info!("fetching GKE access token from {}", self.args.gcloud);
        }
        let out = tokio::process::Command::new(&self.args.gcloud)
            .args(["config", "config-helper", "--format=json"])
            .kill_on_drop(true)
            .output()
            .await
            .with_context(|| format!("running {}", self.args.gcloud))?;
        if !out.status.success() {
            bail!(
                "gcloud config config-helper failed ({}): {}",
                out.status,
                String::from_utf8_lossy(&out.stderr).trim()
            );
        }
        let helper: ConfigHelper =
            serde_json::from_slice(&out.stdout).context("parsing gcloud config-helper output")?;
        let expiry =
            humantime::parse_rfc3339(&helper.credential.token_expiry).with_context(|| {
                format!("parsing token expiry {:?}", helper.credential.token_expiry)
            })?;
        let token = helper.credential.access_token;
        *cached = Some(GcloudToken {
            token: token.clone(),
            expiry,
        });
        Ok(token)
    }

    /// Polls every context in parallel and waits for all of them, each
    /// bounded by --timeout. The kubeconfig is re-read every time, so
    /// contexts added or removed on disk are picked up without a restart.
    async fn poll_all(self: &Arc<Self>) {
        let kubeconfig = match Kubeconfig::read_from(&self.kubeconfig) {
            Ok(k) => k,
            Err(e) => {
                warn!(
                    "error loading kubeconfig {}: {e}",
                    self.kubeconfig.display()
                );
                return;
            }
        };
        let contexts: Vec<String> = kubeconfig.contexts.iter().map(|c| c.name.clone()).collect();

        self.results
            .write()
            .await
            .retain(|name, _| contexts.contains(name));

        let mut tasks = Vec::new();
        for name in contexts {
            let this = Arc::clone(self);
            let kubeconfig = kubeconfig.clone();
            tasks.push(tokio::spawn(async move {
                let res = this.poll_with_timeout(&name, kubeconfig).await;
                this.results.write().await.insert(name, res);
            }));
        }
        for t in tasks {
            let _ = t.await;
        }
    }

    /// Runs poll_cluster under --timeout. On timeout the poll future is
    /// dropped, which cancels its requests and kills a running gcloud.
    async fn poll_with_timeout(&self, name: &str, kubeconfig: Kubeconfig) -> ClusterResult {
        let start = Instant::now();
        let outcome =
            tokio::time::timeout(self.args.timeout, self.poll_cluster(name, kubeconfig)).await;
        let (up, namespaces) = match outcome {
            Ok(Ok(ns)) => (true, ns),
            Ok(Err(e)) => {
                warn!("cluster {name}: {e:#}");
                (false, zero_stats())
            }
            Err(_) => {
                warn!("cluster {name}: timed out after {:?}", self.args.timeout);
                (false, zero_stats())
            }
        };
        let duration = start.elapsed();
        if self.args.verbose {
            info!("cluster {name}: up={up} in {duration:.3?}");
        }
        ClusterResult {
            up,
            duration,
            polled_at: SystemTime::now(),
            namespaces,
        }
    }

    async fn client_for(&self, name: &str, kubeconfig: Kubeconfig) -> Result<Client> {
        let is_gke = context_user(&kubeconfig, name)
            .and_then(|u| u.exec.as_ref())
            .and_then(|e| e.command.as_deref())
            .is_some_and(|c| c.rsplit('/').next() == Some(GKE_PLUGIN));

        let opts = KubeConfigOptions {
            context: Some(name.to_string()),
            ..Default::default()
        };
        let mut config = Config::from_custom_kubeconfig(kubeconfig, &opts)
            .await
            .context("client config")?;
        config.connect_timeout = Some(self.args.timeout);
        config.read_timeout = Some(self.args.timeout);
        if is_gke {
            let token = self.gke_token().await.context("GKE access token")?;
            config.auth_info = AuthInfo {
                token: Some(token.into()),
                ..Default::default()
            };
        }
        Client::try_from(config).context("client")
    }

    /// Reads one cluster: the whole of it, plus --namespace if it exists.
    async fn poll_cluster(
        &self,
        name: &str,
        kubeconfig: Kubeconfig,
    ) -> Result<BTreeMap<String, Stats>> {
        let client = self.client_for(name, kubeconfig).await?;

        let nodes = Api::<Node>::all(client.clone())
            .list(&ListParams::default())
            .await
            .context("listing nodes")?;
        let mut node_stats = Stats::default();
        for n in &nodes.items {
            let Some(status) = &n.status else { continue };
            let res = |list: &Option<BTreeMap<String, Quantity>>, key: &str| {
                list.as_ref()
                    .and_then(|l| l.get(key))
                    .map_or(0.0, |q| quantity(q).ceil())
            };
            node_stats.total_cpu += res(&status.capacity, "cpu");
            node_stats.total_memory += res(&status.capacity, "memory");
            node_stats.total_storage += res(&status.capacity, "storage");
            node_stats.allocatable_cpu += res(&status.allocatable, "cpu");
            node_stats.allocatable_memory += res(&status.allocatable, "memory");
            node_stats.allocatable_storage += res(&status.allocatable, "storage");
        }

        let mut result = BTreeMap::new();
        result.insert(
            ALL_NAMESPACES.to_string(),
            self.namespace_stats(&client, None, node_stats).await?,
        );

        let ns = &self.args.namespace;
        if !ns.is_empty() {
            let exists = Api::<Namespace>::all(client.clone())
                .get_opt(ns)
                .await
                .with_context(|| format!("getting namespace {ns}"))?
                .is_some();
            if exists {
                result.insert(
                    ns.clone(),
                    self.namespace_stats(&client, Some(ns), node_stats).await?,
                );
            }
        }
        Ok(result)
    }

    /// Pod counts, requests and usage for ns (None is all namespaces). Node
    /// capacity is cluster-wide and copied in as is, like the original did
    /// for the per-namespace series.
    async fn namespace_stats(
        &self,
        client: &Client,
        ns: Option<&str>,
        nodes: Stats,
    ) -> Result<Stats> {
        let mut s = nodes;
        let pods: Api<Pod> = match ns {
            Some(ns) => Api::namespaced(client.clone(), ns),
            None => Api::all(client.clone()),
        };
        for pod in pods
            .list(&ListParams::default())
            .await
            .context("listing pods")?
            .items
        {
            s.total += 1.0;
            let (mut cpu, mut mem) = (0.0, 0.0);
            for c in pod.spec.iter().flat_map(|spec| &spec.containers) {
                let requests = c.resources.as_ref().and_then(|r| r.requests.as_ref());
                cpu += requests.and_then(|r| r.get("cpu")).map_or(0.0, quantity);
                mem += requests.and_then(|r| r.get("memory")).map_or(0.0, quantity);
            }
            match pod.status.as_ref().and_then(|st| st.phase.as_deref()) {
                Some("Running") => {
                    s.running += 1.0;
                    s.allocated_cpu += cpu;
                    s.allocated_memory += mem;
                }
                Some("Pending") => {
                    s.pending += 1.0;
                    s.pending_cpu += cpu;
                    s.pending_memory += mem;
                }
                _ => {}
            }
        }

        // metrics-server may be missing or not ready; that is not a reason
        // to call the cluster unreachable, so usage is just left at 0.
        let uri = match ns {
            Some(ns) => format!("/apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods"),
            None => "/apis/metrics.k8s.io/v1beta1/pods".to_string(),
        };
        let req = http::Request::get(uri).body(Vec::new())?;
        match client.request::<PodMetricsList>(req).await {
            Ok(list) => {
                for c in list.items.iter().flat_map(|pm| &pm.containers) {
                    s.used_cpu += c.usage.get("cpu").map_or(0.0, quantity);
                    s.used_memory += c.usage.get("memory").map_or(0.0, quantity);
                }
            }
            Err(e) => warn!(
                "error getting pod metrics (namespace {:?}): {e}",
                ns.unwrap_or("")
            ),
        }
        Ok(s)
    }

    /// Builds a registry from the latest poll results and encodes it with
    /// the prometheus crate. A fresh registry per scrape means a context
    /// removed from the kubeconfig simply stops being reported.
    async fn render(&self, with_process: bool) -> Result<(String, String)> {
        let registry = Registry::new();
        let gauge = |name: &str, help: &str, labels: &[&str]| -> Result<GaugeVec> {
            let g = GaugeVec::new(Opts::new(name, help), labels)?;
            registry.register(Box::new(g.clone()))?;
            Ok(g)
        };
        let status = gauge(
            "kci_k8s_cluster_status",
            "Cluster status",
            &["cluster", "namespace", "status"],
        )?;
        let up = gauge(
            "kci_k8s_cluster_up",
            "1 if the last poll of the cluster succeeded, 0 if it failed or timed out",
            &["cluster"],
        )?;
        let duration = gauge(
            "kci_k8s_cluster_poll_duration_seconds",
            "Time the last poll of the cluster took",
            &["cluster"],
        )?;
        let timestamp = gauge(
            "kci_k8s_cluster_poll_timestamp_seconds",
            "Unix time of the last poll of the cluster",
            &["cluster"],
        )?;
        if with_process {
            registry.register(Box::new(ProcessCollector::for_self()))?;
        }

        for (cluster, res) in self.results.read().await.iter() {
            up.with_label_values(&[cluster])
                .set(f64::from(u8::from(res.up)));
            duration
                .with_label_values(&[cluster])
                .set(res.duration.as_secs_f64());
            let ts = res
                .polled_at
                .duration_since(UNIX_EPOCH)
                .map_or(0.0, |d| d.as_secs() as f64);
            timestamp.with_label_values(&[cluster]).set(ts);
            for (ns, s) in &res.namespaces {
                for (name, v) in s.values() {
                    status
                        .with_label_values(&[cluster.as_str(), ns, name])
                        .set(v);
                }
            }
        }

        let encoder = TextEncoder::new();
        let body = encoder.encode_to_string(&registry.gather())?;
        Ok((encoder.format_type().to_string(), body))
    }
}

fn context_user<'a>(kubeconfig: &'a Kubeconfig, context: &str) -> Option<&'a AuthInfo> {
    let user = kubeconfig
        .contexts
        .iter()
        .find(|c| c.name == context)?
        .context
        .as_ref()?
        .user
        .as_ref()?;
    kubeconfig
        .auth_infos
        .iter()
        .find(|a| &a.name == user)?
        .auth_info
        .as_ref()
}

#[derive(Deserialize)]
struct PodMetricsList {
    items: Vec<PodMetrics>,
}

#[derive(Deserialize)]
struct PodMetrics {
    #[serde(default)]
    containers: Vec<ContainerMetrics>,
}

#[derive(Deserialize)]
struct ContainerMetrics {
    #[serde(default)]
    usage: BTreeMap<String, Quantity>,
}

/// Parses a Kubernetes resource quantity ("250m", "1.5", "128Mi", "1e3",
/// "12345n") into a float. Unparseable values count as 0.
fn quantity(q: &Quantity) -> f64 {
    parse_quantity(&q.0).unwrap_or_else(|| {
        warn!("cannot parse quantity {:?}", q.0);
        0.0
    })
}

fn parse_quantity(s: &str) -> Option<f64> {
    let s = s.trim();
    let split = s
        .char_indices()
        .find(|&(i, c)| !(c.is_ascii_digit() || c == '.' || ((c == '+' || c == '-') && i == 0)))
        .map_or(s.len(), |(i, _)| i);
    let (num, suffix) = s.split_at(split);
    let num: f64 = num.parse().ok()?;
    let mult = match suffix {
        "" => 1.0,
        "n" => 1e-9,
        "u" => 1e-6,
        "m" => 1e-3,
        "k" => 1e3,
        "M" => 1e6,
        "G" => 1e9,
        "T" => 1e12,
        "P" => 1e15,
        "E" => 1e18,
        "Ki" => 1024f64,
        "Mi" => 1024f64.powi(2),
        "Gi" => 1024f64.powi(3),
        "Ti" => 1024f64.powi(4),
        "Pi" => 1024f64.powi(5),
        "Ei" => 1024f64.powi(6),
        exp if exp.starts_with(['e', 'E']) => 10f64.powi(exp[1..].parse().ok()?),
        _ => return None,
    };
    Some(num * mult)
}

async fn metrics(State(exporter): State<Arc<Exporter>>) -> Response {
    match exporter.render(true).await {
        Ok((content_type, body)) => ([(header::CONTENT_TYPE, content_type)], body).into_response(),
        Err(e) => {
            warn!("encoding metrics: {e:#}");
            (StatusCode::INTERNAL_SERVER_ERROR, format!("{e:#}\n")).into_response()
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    tracing_subscriber::fmt()
        // Library internals (kube, hyper) log at DEBUG; --verbose only adds
        // this exporter's own per-cluster lines, which are logged at INFO.
        .with_max_level(tracing::Level::INFO)
        .with_ansi(false)
        .with_target(false)
        .with_writer(std::io::stderr)
        .init();

    let kubeconfig = match &args.kubeconfig {
        Some(p) => p.clone(),
        None => PathBuf::from(std::env::var("HOME").map_err(|_| anyhow!("HOME is not set"))?)
            .join(".kube/config"),
    };
    let exporter = Arc::new(Exporter {
        args,
        kubeconfig,
        results: RwLock::new(BTreeMap::new()),
        gcloud_token: Mutex::new(None),
    });

    if exporter.args.dryrun {
        exporter.poll_all().await;
        print!("{}", exporter.render(false).await?.1);
        return Ok(());
    }

    let poller = Arc::clone(&exporter);
    tokio::spawn(async move {
        loop {
            poller.poll_all().await;
            tokio::time::sleep(poller.args.interval).await;
        }
    });

    let app = Router::new()
        .route(&exporter.args.path, get(metrics))
        .with_state(Arc::clone(&exporter));
    let listener = tokio::net::TcpListener::bind(&exporter.args.listen)
        .await
        .with_context(|| format!("listening on {}", exporter.args.listen))?;
    info!(
        "listening on {}{}, polling {} every {:?}",
        exporter.args.listen,
        exporter.args.path,
        exporter.kubeconfig.display(),
        exporter.args.interval
    );
    axum::serve(listener, app).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::parse_quantity;

    #[test]
    fn quantities() {
        let cases = [
            ("1", 1.0),
            ("250m", 0.25),
            ("1.5", 1.5),
            ("12345678n", 0.012345678),
            ("128Mi", 134217728.0),
            ("1Gi", 1073741824.0),
            ("2k", 2000.0),
            ("1e3", 1000.0),
            ("41973641216", 41973641216.0),
            ("40990076Ki", 41973837824.0),
        ];
        for (s, want) in cases {
            let got = parse_quantity(s).unwrap();
            assert!(
                (got - want).abs() < 1e-9 * want.abs().max(1.0),
                "{s}: got {got}, want {want}"
            );
        }
        assert_eq!(parse_quantity("abc"), None);
        assert_eq!(parse_quantity("1Xi"), None);
    }
}
