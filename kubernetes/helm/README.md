# KernelCI Helm charts

API and Pipeline are independent Helm releases in separate namespaces. This
directory is a deployment workspace, not an umbrella chart.

- `deploy.sh`: initial deployment, configuration changes and operational commands.
- `api-production-update.sh`: build/publish images and update installed releases.
- `config.sh`: shared environment loading and credential handling.
- `charts/api`: API, Redis, optional MongoDB, API ConfigMap and Gateway resources, and optional ClusterIssuer.
- `charts/pipeline`: Pipeline workloads, callback Service and Gateway resources, and ConfigMaps.
- `manifests/envoy/gatewayclass.yaml`: standalone, cluster-scoped GatewayClass manifest, managed separately from the Helm releases with kubectl.
- `environments/<deployment>/<environment>/deployment.yaml`: deployment orchestration settings.
- `environments/<deployment>/<environment>/api-values.yaml`: API overrides.
- `environments/<deployment>/<environment>/pipeline-values.yaml`: Pipeline overrides.

Use this workspace for the upstream multi-environment Helm deployment implementation. The
older `api-pipeline-deploy.sh` script in the parent directory is a separate
implementation with a different configuration and networking behaviour.

Shell examples below run from the repository root unless stated otherwise. Both
wrapper scripts resolve their files relative to their own location, so they can
also be invoked by absolute path from another working directory. Execute the
scripts with Bash rather than sourcing them into your shell.

## Template structure

Both charts keep each workload in a named template. The API uses `api.yaml`
and `redis.yaml`, plus optional `mongo.yaml`; Pipeline uses `timeout.yaml`, `closing.yaml`,
`holdoff.yaml`, `scheduler-k8s.yaml`, `scheduler-shell.yaml`,
`scheduler-lava.yaml`, `tarball.yaml`, `trigger.yaml`,
`pipeline-kcidb.yaml`, and `lava-callback.yaml`. A workload's Service,
where needed, stays in the same file. ConfigMaps, Gateways, and the optional
issuer have separate templates. Helpers contain labels and ConfigMap naming.

Pipeline `components.<name>` values configure commands, environment variables,
resources, replicas (defaulting to one), and optional health ports. Commands
default to their existing invocations in the chart's `values.yaml`. Override
the complete command list in an environment's `pipeline-values.yaml` (or another
values file passed with `-f`), for example:

```yaml
components:
  timeout:
    command:
      - ./src/timeout.py
      - --settings=/secrets/kernelci.toml
      - --yaml-config=/config
      - run
      - --mode=timeout
      - --verbose
```

Helm replaces the entire list rather than appending arguments. LAVA callback
uses `components.lava-callback.command` and `components.lava-callback.args`;
its enablement and replicas remain under `lavaCallback`. API-wait and
credentials init-container commands remain defined in the templates.

Set the LAVA instance and callback URL under
`components.scheduler-lava.env.KCI_INSTANCE` and
`components.scheduler-lava.env.KCI_INSTANCE_CALLBACK`. The environment-level
`environment.instance` and `environment.callbackUrl` keys are not used by these
templates. Callback URLs inside selected Pipeline configuration files must also
be correct for the target environment; the wrapper uploads their contents unchanged.

### Component images

Each Pipeline component has its own explicit `image.repository`, `image.tag`,
and `image.pullPolicy` in the chart's `values.yaml`. Workloads default to
`kernelci/kernelci:pipeline`, except LAVA callback, which defaults to
`kernelci/kernelci:lava-callback`. There is no shared workload image setting or
fallback helper. Environment values override individual fields for each
component through normal Helm values merging.

For example, in an environment's `pipeline-values.yaml`:

```yaml
components:
  scheduler-lava:
    image:
      tag: "pipeline@sha256:<digest>"
  pipeline-kcidb:
    image:
      repository: registry.example.com/kernelci
      tag: pipeline
      pullPolicy: IfNotPresent
```

The chart renders `<repository>:<tag>`. Both plain tags and tags with a digest
are supported, for example `kernelci/kernelci:pipeline` or
`kernelci/kernelci:pipeline@sha256:<digest>`. Supply the full digest, including
its algorithm; when a digest is supplied, it selects the image to pull.

Component image settings affect main workload containers only. Set
`credentialsInit.image.repository`, `credentialsInit.image.tag`, and
`credentialsInit.image.pullPolicy` for the scheduler credentials init
containers. API-wait init containers use `waitForApi.image`.

`environments/kernelci-org/prod/pipeline-values.yaml` pins each workload to its
upstream `kernelci-pipeline/main` deployment image. Update these values alongside
upstream image changes.

The former `recreate`, `credentials`, `sourceVolume`, and `kcidb` flags
are replaced by explicit YAML in the workloads that need them. Change those
structural details in the corresponding template. Adding a workload means
adding its named template and values entry; a values entry alone does not create
a Deployment.

## Deployment

Each `environments/<deployment>/<environment>/` directory contains:

- `deployment.yaml`: cluster context, release namespaces, Azure provisioning,
  Envoy chart/image settings and cert-manager installation, source repositories/branches, build image
  prefix, Pipeline configuration include list, and deployment/update timeout.
- `api-values.yaml`: API, MongoDB, Redis, Gateway and issuer configuration.
- `pipeline-values.yaml`: Pipeline workloads, endpoints and Gateway configuration.
- `secrets/`: local, ignored credentials for this target (`deploy.cfg`, TOML, keys and archives).

Both scripts share `config.sh`. They load the selected `deployment.yaml` and
validate all three YAML files before contacting the cluster. Application values
are passed directly to Helm; the script reads chart defaults merged with the
selected environment only where it needs those settings for orchestration.
YAML values take precedence over old shell configuration. Select `DEPLOYMENT` and
`ENVIRONMENT` through environment variables; defaults are `kernelci-org` and `prod`.
There are no corresponding command-line flags. Non-secret settings in `deploy.cfg`
are ignored.

The supplied targets are:

| Target | Sources and networking | MongoDB and TLS |
| --- | --- | --- |
| `kernelci-org/prod` | Public GitHub repositories on `main`; public Azure Gateways | External MongoDB through a Secret; chart-managed certificates and staging ACME issuer |

Review the selected environment files before deployment. Source branches,
workload image repositories and `build.imagePrefix` are separate settings.

### Prerequisites and initial deployment

Use Bash with `mapfile -d` support, kubectl, Helm and Mike Farah yq v4. The scripts
also use Unix utilities including GNU-compatible `base64`, `find` and `sort -z`.
The selected Kubernetes context must already exist in your kubeconfig.
`full` additionally requires authenticated Azure CLI access, and Git when cloning
a missing Pipeline checkout. The wrapper's `full` path performs Azure provisioning
and network-role assignments even when both Gateways use private addresses;
use direct charts for infrastructure managed separately.

For the kernelci-org prod environment, invoke commands with:

```sh
DEPLOYMENT=kernelci-org ENVIRONMENT=prod kubernetes/helm/deploy.sh full
```

Relative `pipelineConfig.include[].directory` paths in `deployment.yaml` are resolved from
`kubernetes/helm`, independent of the caller's working directory. The default
`kernelci-pipeline/config` directory can be populated by cloning the configured
Pipeline repository during `full`; custom directories must already exist. An
existing checkout is reused without fetching or switching branches. Other
commands require the selected configuration directories to exist already.
`apply`, `config` and direct `update` accept an optional configuration-directory
argument when exactly one include entry is configured; multiple entries make that
override ambiguous and are rejected.

### Credentials

Copy `deploy.cfg.example` to the selected directory as
`environments/<deployment>/<environment>/secrets/deploy.cfg` (ignored by Git).
It contains credentials only: `API_SECRET_KEY`, `API_TOKEN`, `EMAIL_PASSWORD`,
and, when selected through Secret references, `MONGO` and `KCIDB_REST`.
Keep `kernelci-secrets.toml`, `k8s.tgz`, optional `id_rsa` and
`kcidb-credentials.json` in that same `secrets/` directory; they are
local and ignored by Git. Archive uploads (`k8s-new.tgz`) and downloads
(`k8s-old.tgz`), and TOML downloads, also use that directory. The entire
environment-level `secrets/` directory is ignored by Git.
Credentials are loaded in isolation and cannot change YAML settings
or select a different deployment. The updater uses registry credentials from
Docker's normal configuration and does not load `deploy.cfg`.

Only `full`, `token` and `pipeline-credentials` load `deploy.cfg`. Plain shell
assignments are sufficient; existing `export` assignments are also supported.
You do not need to source this file or export credentials in your calling shell.
These commands require the selected file and discard inherited credential
variables. There is no fallback to a shared `deploy.cfg` or shared credential files.

Credential writes (`full`, `token`, `pipeline-credentials`,
`deploy_secret_toml_only`, `update_k8s_credentials`) require both `DEPLOYMENT`
and `ENVIRONMENT` explicitly. Required inputs for a full credential refresh are
checked before provisioning or cluster changes. Configuration-only commands
still work without `deploy.cfg`.

To migrate, identify which target each existing local secret belongs to, then
move it into that target's `secrets/` directory. Do not copy shared production
credentials into every environment. Files in the old environment root or shared
script directory are ignored by the Helm wrapper.
The legacy scripts under `kubernetes/` retain their existing credential behavior.

The handled credential variables are `API_SECRET_KEY`, `EMAIL_PASSWORD`,
`API_TOKEN`, `MONGO`, `KCIDB_REST` and `LAVA_KEY`. The loader accepts `LAVA_KEY`,
but the current Secret-creation commands do not use it. Inherited credentials are
cleared at startup; only the selected file supplies values. Loaded credentials
remain shell-local, preventing inheritance by external commands. After successful credential creation
they are unset; an `EXIT` trap clears them on other exits, including failures.
Secret argument arrays are local, and temporary TOML and MongoDB buffers are kept
out of child-process environments. The backup MongoDB URI is unset after use.
The updater discards inherited deployment credentials at startup because it does
not use them.

These scripts cannot unset variables in the shell that invoked them; clear any
credentials you exported there separately. Credential values required by existing
`kubectl` commands are still supplied as command arguments.

For a credential-bearing external Mongo URL, configure API values as follows:

```yaml
mongo:
  enabled: false
config:
  emailSender: kernelci@example.com
  mongoServiceSecretKey: mongo-service
waitForMongo:
  host: mongo.example.com
```

Set the corresponding `MONGO` credential in `deploy.cfg`. The wrapper stores it
under that key in the API's `existingSecret`. For a URL without credentials, use
`config.mongoService` directly instead and leave `mongoServiceSecretKey` empty.

Likewise, set `environment.kcidbRestSecretKey: kcidb-rest` in Pipeline values and
put `KCIDB_REST` in `deploy.cfg`. The wrapper stores it in `secrets.pipeline`.
For a public endpoint, use `environment.kcidbRest` and leave the Secret key empty.
Credential-bearing URLs are referenced through Kubernetes Secrets rather than
included in Helm values or rendered ConfigMaps. The Secret names themselves are
read from the charts, including when creating or retrieving credentials.

The credential-creation commands require `API_SECRET_KEY`, `EMAIL_PASSWORD`,
`API_TOKEN`, `kernelci-secrets.toml` and `k8s.tgz` even when refreshing an existing
installation. They optionally include `id_rsa` and
`kcidb-credentials.json` in the Pipeline Secret when those files exist.
`deploy_secret_toml_only` updates only the TOML key and does not load `deploy.cfg`.

### Applying configuration

`full` creates namespaces, credentials and cluster services, provisions public
IP/DNS resources where requested by Gateway annotations, and deploys both charts.
It renders both charts before making cluster changes and preserves existing
namespaces. It does not build application images. Required credentials and local
credential files must be present for initial deployment; their checks occur during
credential creation, after namespace/network provisioning has begun. Failures do
not roll back earlier steps. Omitting the command defaults to `full`, so use an
explicit command when invoking the script.

After editing the environment YAML files, apply configuration without image
builds or infrastructure provisioning:

```sh
DEPLOYMENT=kernelci-org ENVIRONMENT=prod bash kubernetes/helm/deploy.sh apply
```

`apply` resets each release to chart defaults plus the selected environment values,
then adds only generated Pipeline configuration and derived cross-release settings.
It renders both charts before upgrading either release, upgrades API first and
waits for readiness. Existing settings supplied only through previous `--set`
arguments must be moved into the environment files before using this command.
It does not create Secrets, install controllers, or allocate IP addresses. Use
`full` for initial deployment and `pipeline-credentials` to create or refresh
credentials before applying new Secret references.

The Pipeline API wait URL is derived from the API namespace unless
`waitForApi.url` is explicitly set in the environment. Issuer parent namespaces
and Pipeline hostnames are similarly derived from the selected target and
Pipeline values. Issuer email and ACME server remain in API values.

### Command reference

Run `bash kubernetes/helm/deploy.sh COMMAND` with the selected deployment and
environment. `--help` prints usage without requiring environment files or tools.
Other commands load and validate all three environment YAML files and require
kubectl, Helm, yq and the selected Kubernetes context.

| Command | Behaviour |
| --- | --- |
| `show-deployments` | List API and Pipeline Deployments in the selected context/namespaces. |
| `full` | Create/update namespaces, provision networking, apply credentials, install controllers and deploy both charts. |
| `apply [CONFIG_DIRECTORY]` | Apply chart defaults plus selected environment values, without provisioning infrastructure or credentials. |
| `update [CONFIG_DIRECTORY]` | Update existing releases using supplied digests and selected Pipeline configuration; preserve installed settings. |
| `config [CONFIG_DIRECTORY]`, `pipeline-configmap` | Refresh chart-managed Pipeline configuration, preserving installed values; only `config` accepts a directory argument. |
| `token` | Apply all credentials, refresh Pipeline configuration and apply the Pipeline `environment` values. |
| `pipeline-credentials` | Apply all credentials and the Pipeline `environment` values. |
| `deploy_secret_toml_only` | Patch only `kernelci.toml` in the Pipeline Secret from the local TOML file. |
| `retrieve_secrets_toml` | Save the installed Pipeline TOML key to local `kernelci-secrets.toml`. |
| `retrieve_k8s_credentials` | Save the installed build-cluster credentials to local `k8s-old.tgz`. |
| `update_k8s_credentials` | Create/update the build-cluster Secret from local `k8s-new.tgz`. |
| `backup-config` | Save the ConfigMap named `pipeline-configmap` to local `pipeline-configmap.yaml`. |
| `cert` | Enable/update the API release's shared ClusterIssuer using the selected issuer settings. |
| `backup-mongo`, `mongo-shell` | Back up or open a shell in an in-cluster pod labelled `app=mongo`. |
| `api-restart-pods`, `pipeline-restart-pods` | Roll out a restart of the API Deployment or all Pipeline Deployments. |
| `delete-all` | Delete both application namespaces and the configured Envoy controller namespace, including their namespaced resources and PVCs. |
| `restore-mongo` | Print the existing unimplemented-operation message. |

`full`, `apply` and `update` use `update.timeout` for application release waits.
The `config`/`pipeline-configmap` refreshes and controller installations use a fixed
`10m` timeout. The issuer and environment-only upgrades do not request Helm waits.
`delete-all` leaves cluster-scoped resources such as the GatewayClass and
ClusterIssuer; it is not a complete uninstall of shared controllers and CRDs.

When using `pipelineConfig.existingConfigMap`, manage that ConfigMap separately:
`config`, `pipeline-configmap` and `token` retain its reference rather than updating
its contents. `backup-config` currently always reads `pipeline-configmap`, even
when a different external name is configured. `update` explicitly switches the
release back to chart-managed configuration.

### Optional MongoDB

Configure MongoDB solely in the environment's `api-values.yaml`:

```yaml
mongo:
  enabled: true
  image:
    repository: mongo
    tag: "8"
  resources:
    requests:
      memory: 10Gi
      cpu: "2"
  persistence:
    size: 60Gi
    storageClass: null  # Cluster default; a class name or "" is also supported.
```

Apply the values with `deploy.sh apply` (or `full` on initial deployment). This
creates a single-replica MongoDB 8 StatefulSet and a namespace-local `mongo`
Service. The API uses `mongodb://mongo:27017` and waits for that Service.
External Mongo settings and Secret references are ignored while enabled.
There is no `MONGO_ENABLED` setting in `deploy.cfg`.

Data is mounted at `/data/db` from PVC `data-mongo-0`. Kubernetes retains this
PVC when Mongo is disabled or the release is uninstalled, and reuses it when
Mongo is re-enabled. Deleting its namespace still deletes the PVC. Choose the
storage size/class before deployment; changing an existing claim template
requires a separate storage expansion or migration. The instance has no MongoDB
authentication and is exposed through a cluster-internal Service.

To return to an external database, set `mongo.enabled: false`, configure its URL
or Secret reference, and apply. Database contents are not migrated in either
direction. `update` retains the installed database mode while refreshing images
and Pipeline configuration.

`backup-mongo` resolves the installed API Deployment's `MONGO_SERVICE` reference
from its ConfigMap or Secret, without requiring a running API pod. It and
`mongo-shell` require an in-cluster pod
labelled `app=mongo`; external mode does not create such a pod.

### Production updates

`api-production-update.sh` is the Helm equivalent of the legacy production
updater. Run it after the API and Pipeline releases have been installed with
`deploy.sh full`. It reads the same selected environment YAML files,
Kubernetes context, namespaces, and Pipeline configuration include list as
`deploy.sh`. It can be invoked from any working directory:

```sh
bash kubernetes/helm/api-production-update.sh
# Use already published images, for example after CI has built them:
bash kubernetes/helm/api-production-update.sh workflow
```

The normal run clones Core, API, and Pipeline into a temporary directory,
records their revisions, and uses Core's `kci docker build` to build and push
the base, API, Pipeline, and LAVA callback images. Existing source checkouts
are preserved. Both modes pull the three deployment images, report their
digests and creation dates, fetch fresh Pipeline configuration, and invoke
`deploy.sh update`. The temporary files are removed on exit.

Git, Docker, kubectl, Helm, Python 3, and
[Mike Farah yq v4](https://github.com/mikefarah/yq) must already be installed.
Building uses `python:3.11`, the host Docker socket,
and `$HOME/.docker` credentials, as in the legacy updater. Configure Docker
registry authentication before running. No legacy `configmap.yaml` is needed:
the API keeps its installed Helm configuration.

Source repositories and branches come from `sources.{core,api,pipeline}` in
`deployment.yaml`. `build.imagePrefix` controls the build/pull destination, such
as `kernelci/` or `registry.example.com/team/`. Images are published/pulled as
`<imagePrefix>kernelci:{api,pipeline,lava-callback}`. Fresh configuration comes
from that run's Pipeline checkout, including in `workflow` mode. Include directories
written as `kernelci-pipeline` or `kernelci-pipeline/...` resolve against that fresh
checkout. Other relative directories still resolve from `kubernetes/helm`, and
absolute directories retain their configured locations.

The update command requires both releases to exist and uses `--reuse-values`
to retain their installed settings, including credentials references,
networking, commands, resources, and replica counts. It pins the API, every
Pipeline workload (including timeout, closing, and holdoff), LAVA callback,
and scheduler credentials init containers to the resolved digests. It refreshes
the chart-managed Pipeline ConfigMap using the selected configuration files;
this switches an external `pipelineConfig.existingConfigMap` to chart-managed
configuration if one was configured. It does not rerun infrastructure or
credential provisioning, or reapply environment values files to the releases.

The updater uses `<build.imagePrefix>kernelci` as the repository for all these
image pins, even if installed component repositories differ. Set the build prefix
to the intended publication/deployment registry before running it. The directory
of an existing local checkout does not determine the sources used for this update.

After validating the digests and configuration, and before the first Helm
upgrade, `deploy.sh update` also writes these image repositories and
`tag@sha256:<digest>` pins into the selected environment's
`environments/${DEPLOYMENT}/${ENVIRONMENT}/api-values.yaml` and
`pipeline-values.yaml`. This includes the scheduler credentials init image.
Missing image keys are added, and unrelated values are retained. Both YAML
edits are staged before replacing the files; a parsing error stops deployment
without changing either file. The files retain the attempted pins if a Helm
upgrade subsequently fails, matching the legacy updater's behavior. Other
deployments/environments and chart defaults are left unchanged. These edits
remain local working-tree changes; the updater does not commit them.

API is upgraded first with `--wait`. Pipeline is upgraded with the trigger
scaled to zero, and the trigger's previous replica count is restored in a
second upgrade after the other workloads are ready. Each upgrade waits up to
`update.timeout` in `deployment.yaml`. These waits check Kubernetes readiness;
they do not exercise complete job processing. Upgrades of the two releases
are independent, with no automatic rollback. A failed Pipeline update may
leave the trigger paused; fix the failure and rerun. Its original replica
count is saved in the release's `updateTriggerReplicas` value until restored.

To update directly using previously resolved digests and a local configuration
directory, replace the placeholders below with full SHA-256 digests:

```sh
API_IMAGE_DIGEST='sha256:<64-hex-characters>' \
PIPELINE_IMAGE_DIGEST='sha256:<64-hex-characters>' \
LAVA_CALLBACK_IMAGE_DIGEST='sha256:<64-hex-characters>' \
bash kubernetes/helm/deploy.sh update /path/to/pipeline/config
```

For direct updates, the image repository defaults to `<build.imagePrefix>kernelci`;
the updater passes its resolved repository through `UPDATE_IMAGE_REPOSITORY`. Direct `update` without a directory
argument uses all entries in `pipelineConfig.include`.

### Selecting Pipeline configuration files

Configure `pipelineConfig.include` in the environment's `deployment.yaml`:

```yaml
pipelineConfig:
  include:
    - directory: kernelci-pipeline/config
      files:
        - fragments.yaml
        - pipeline.yaml
        - trees/kernelci-org-internal.yaml
    - directory: kernelci-pipeline/extra-config
      files: all
```

Each directory can be absolute or relative to `kubernetes/helm`, independently
of the caller's working directory. File lists contain literal paths relative to
their entry's directory; absolute file paths, parent traversal and glob patterns
are not supported. `files: all` recursively includes `.yaml` and `.yml` files
in sorted order. Other file types are ignored.

The include list and each file list must be nonempty. Every directory and file
must exist and be readable. Selected files must have unique basenames across
all entries, since each basename becomes a ConfigMap key; duplicates fail
instead of overriding one another. Include any files defining jobs, runtimes,
or other objects referenced by your selected configuration.

The selection applies to `full`, `apply`, `config`, `pipeline-configmap`, `token`,
and `update`, including the production updater, when supplying chart-managed
configuration. With exactly one entry, `apply`, `config` and direct `update` accept
a directory override while retaining the configured file selection, for example:

```sh
bash kubernetes/helm/deploy.sh config /path/to/config
```

A directory argument with multiple entries is an error. `full` and the
`pipeline-configmap` alias use the directories from `deployment.yaml`.

### Direct chart deployment

For direct chart use, install the required controllers/CRDs and create the
referenced Secrets and external Pipeline ConfigMap first, then run from the
repository root:

```sh
helm upgrade --install kernelci-api kubernetes/helm/charts/api \
  -n kernelci-api --create-namespace \
  -f kubernetes/helm/environments/kernelci-org/prod/api-values.yaml

helm upgrade --install kernelci-pipeline kubernetes/helm/charts/pipeline \
  -n kernelci-pipeline --create-namespace \
  -f kubernetes/helm/environments/kernelci-org/prod/pipeline-values.yaml \
  --set-string pipelineConfig.existingConfigMap=pipeline-configmap \
  --set-string waitForApi.url=http://api.kernelci-api.svc.cluster.local/
```

For direct use with another API namespace, update `waitForApi.url`. The Pipeline
chart accepts configuration through `pipelineConfig.files` or a pre-created
ConfigMap through `pipelineConfig.existingConfigMap`. Secrets and ConfigMaps
referenced by a workload must exist in that workload's namespace.

These examples use the kernelci-org values' MongoDB StatefulSet, Secret-backed KCIDB
endpoint and externally supplied TLS Secrets. Direct Helm commands do not read
`deployment.yaml` or `deploy.cfg`, provision Azure resources, collect the Pipeline
include list or derive cross-release settings. When switching to a public KCIDB
URL, clear `environment.kcidbRestSecretKey`; when switching to external MongoDB,
disable `mongo.enabled` and configure its URL or Secret reference and wait host.

The optional ClusterIssuer is cluster-scoped and is owned by the API release;
Pipeline references it by name. For chart-managed certificates, ensure
cert-manager and the issuer exist.
The wrapper's `cert` command enables or updates this issuer after both releases
exist. Upgrades and rollbacks of API and Pipeline are otherwise independent.

The wrapper retains the existing commands for configuration, credentials,
backups, shell access, and restarts. An unrecognised command prints usage.
`restore-mongo` remains an unimplemented placeholder.

## Envoy Gateway

The API and Pipeline charts now use Gateway API v1 and Envoy Gateway instead
of Ingress. Each owns a Gateway, EnvoyProxy, HTTPS HTTPRoute, HTTP-to-HTTPS
redirect, BackendTrafficPolicy, and Certificate. Gateway settings use the
`gateway` values key. The former `ingress` key, `className`, and nginx annotations
are no longer used. `gateway.enabled=false` disables these resources.

The external Envoy Helm chart installs the controller and its supporting
resources. The standalone Kubernetes manifest
`manifests/envoy/gatewayclass.yaml` defines the shared, cluster-scoped
`kernelci-envoy` GatewayClass and associates it with the Envoy Gateway controller.
The API and Pipeline Gateways reference this class through
`gatewayClassName: kernelci-envoy`. The wrapper applies the manifest with
`kubectl` after installing the controller and before deploying the application
charts. It is managed separately from the Helm releases; `charts/` contains
Helm charts and `manifests/` contains standalone Kubernetes manifests.

`full` installs Envoy Gateway and its CRDs before deploying the charts.
`envoyGateway.chart` in `deployment.yaml` selects the external chart reference,
`envoyGateway.version` pins its version, and `envoyGateway.namespace` selects
the installation namespace. Check Kubernetes compatibility before installation.
All Envoy settings live in that `envoyGateway` block. Its `values` key must be a
YAML mapping containing native upstream chart values; use `values: {}` for defaults.
The wrapper passes only this mapping to Helm through stdin, excluding the chart
reference, version, namespace and other deployment settings.
`full` installs the chart with defaults plus `envoyGateway.values`,
resetting previous Helm overrides. Move any controller settings supplied only by
previous Helm commands into that mapping before running `full`.
The controller watches the cluster and creates separate proxy Deployments and
Services for each Gateway in its own namespace. Deleting the application
namespaces does not uninstall the shared controller or GatewayClass.

Before direct chart deployment, install the controller and GatewayClass:

```sh
DEPLOYMENT_FILE=kubernetes/helm/environments/kernelci-org/prod/deployment.yaml
yq eval '.envoyGateway.values' "$DEPLOYMENT_FILE" | \
  helm upgrade --install kernelci-envoy \
    "$(yq eval -r '.envoyGateway.chart' "$DEPLOYMENT_FILE")" \
    --version "$(yq eval -r '.envoyGateway.version' "$DEPLOYMENT_FILE")" \
    --kube-context "$(yq eval -r '.kubernetes.context' "$DEPLOYMENT_FILE")" \
    --namespace "$(yq eval -r '.envoyGateway.namespace' "$DEPLOYMENT_FILE")" \
    --create-namespace --reset-values -f - --wait
kubectl --context "$(yq eval -r '.kubernetes.context' "$DEPLOYMENT_FILE")" \
  apply -f kubernetes/helm/manifests/envoy/gatewayclass.yaml
```

The supplied `kernelci-org/prod` environment uses Docker Hub for both the chart
and controller image.

Chart access is required from the deployment runner; container image access is
required from cluster nodes. If authentication is needed, configure Helm registry
login separately from Kubernetes registry Secrets. Reference existing Secrets
through `envoyGateway.values.deployment.envoyGateway.imagePullSecrets`; they
must exist in the Envoy Gateway namespace before installing the chart.
Image tags are independent of the chart version and must remain compatible
with its templates and CRDs. `apply` and `update` operate on API/Pipeline only;
use `full` or the direct Helm command to change the shared controller.

For provider-managed Gateway API CRDs, follow the
[Envoy installation instructions](https://gateway.envoyproxy.io/docs/install/install-helm/)
instead of installing another copy. Upgrading Envoy versions also requires
upgrading CRDs separately; Helm does not upgrade a chart's `crds` directory.

Each chart's `gateway.serviceAnnotations` controls its own networking. For example:

```yaml
gateway:
  serviceAnnotations:
    service.beta.kubernetes.io/azure-load-balancer-internal: "true"
    service.beta.kubernetes.io/azure-load-balancer-ipv4: "10.226.6.30"
```

Use `"false"` for a public load balancer. In public mode, setting
`service.beta.kubernetes.io/azure-dns-label-name` asks `full` to find or create
`<label>-ip` and configure that Azure DNS label. Existing static IPs can be
supplied through the IPv4 annotation. Newly discovered IPs are written back to
the corresponding environment values file, never to `deploy.cfg`. The
resource-group annotation can override `azure.resourceGroup` from `deployment.yaml`;
the latter supplies its default. Without a DNS label, Azure manages the public
LoadBalancer address. `apply` never provisions public IP/DNS resources.

Internal Gateways skip public-IP and DNS provisioning independently of the
other chart. Omit the IPv4 annotation for automatic private-IP allocation or
supply an available subnet address. When changing modes, remove or replace old
IP/DNS annotations in the values file. Internal certificates still require your
existing TLS arrangement or DNS-01.

`externalTrafficPolicy: Local` is preserved. Non-Azure deployments should
override `gateway.serviceAnnotations` appropriately. Use
`gateway.gatewayClassName` to select a separately managed GatewayClass.

Set `gateway.hostnames` to a list of DNS names (replacing `gateway.host`):

```yaml
gateway:
  hostnames:
    - example-api.example.com
    - another-api.example.com
```

All names route to the same Service, including API event subscriptions, and
are included in the certificate and HTTPS redirects. Both charts use this list
format, even with one hostname. Listeners omit a hostname restriction; HTTPRoutes
restrict application traffic to the configured names. Point every name at the
Gateway's load balancer. When providing your own TLS Secret, its certificate
must cover all names.

The API issuer's solver automatically selects `gateway.hostnames`. Set
`issuer.pipelineHostnames` in the API values to the Pipeline chart's hostname
list when enabling the shared issuer directly. The wrapper's `cert` command
reads that list from the selected Pipeline values.

The API buffers requests up to 32MiB and enables gzip; Pipeline buffers up to
128MiB. Configure `gateway.maxRequestBytes` and `gateway.compression` to change
these policies. Buffering consumes proxy memory under concurrent uploads.
Gzip uses Envoy defaults instead of the previous nginx compression tuning.
The old `patch-nginx` command is removed.

The API's `/latest/listen` event endpoint has a separate `api-events` HTTPRoute.
Its request, backend-request, and stream-idle timeouts are disabled so quiet
event subscriptions can remain open. Other API endpoints retain normal Envoy
timeouts. This route takes precedence over the API's catch-all `/` route.

### Certificates

Set `certManager.install: true` or `false` in `deployment.yaml` to control whether
`full` installs or upgrades cert-manager. Installation enables Gateway API
support. This does not change the charts' Certificate, issuer or TLS settings.

Install cert-manager before deploying the charts. To use TLS Secrets without
cert-manager, set `gateway.certificate.create=false` in both charts and
`issuer.enabled=false` in the API chart, and provide the named TLS Secrets yourself.
For existing cert-manager installations, enable Gateway API support using the
configuration for that version and restart the controller after installing the
Gateway API CRDs. The optional wrapper installation sets
`config.gatewayAPI.enabled=true` for current cert-manager versions.
See [cert-manager HTTP-01](https://cert-manager.io/docs/configuration/acme/http01/).

`full`, `apply` and `cert` read issuer email and ACME URL from API values:
`issuer.email` and `issuer.server`. Use
`https://acme-staging-v02.api.letsencrypt.org/directory` for staging or
`https://acme-v02.api.letsencrypt.org/directory` for production. The supplied
environment values use staging, matching the former wrapper default.

Parent namespaces come from `deployment.yaml`; Pipeline hostnames come from
Pipeline values merged with chart defaults. `full` and `apply` preserve
`issuer.enabled`; `cert` explicitly enables the issuer and applies the local
issuer settings while preserving other installed values. Apply Gateway changes
before using `cert` on an existing release. These commands configure Gateway
HTTP-01 solvers.

Direct users with custom namespaces must set `issuer.apiGatewayNamespace` and
`issuer.pipelineGatewayNamespace`. Challenge routes are created alongside the
Certificates and take precedence over the catch-all HTTPS redirect.

Let's Encrypt must reach the hostname on port 80. Internal-only load balancers
generally require DNS-01 instead. Supply that issuer via `gateway.clusterIssuer`
and leave the optional HTTP-01 issuer disabled.

### Existing nginx installations

Plan the cutover before running `full` on an existing installation. The wrapper
does not uninstall the old `ingress-nginx` / `ingress-nginx2` releases. Azure
cannot assign the same IP to both nginx and Envoy Services.

For staged migration, use temporary Envoy IPs and preserve the old Ingress
manifests outside these releases before replacing a chart that owns them.
The wrapper does not delete standalone legacy Ingress resources.
Test HTTPS, API requests, callback POSTs, redirects,
certificates, and upload limits on the new addresses before switching DNS.
TLS Secrets are reused by name; check Certificate ownership before enabling
their management in these charts.

Alternatively, schedule a maintenance window, release the nginx load balancer
IPs, and deploy Envoy with the original static IP annotations in the environment values.
Verify Gateway `Accepted`/`Programmed`, HTTPRoute `Accepted`/`ResolvedRefs`,
and Certificate readiness:

```sh
kubectl -n kernelci-api get gateway,httproute,certificate
kubectl -n kernelci-pipeline get gateway,httproute,certificate
kubectl -n envoy-gateway-system get deployments,services
```

Remove old nginx releases after verifying the new endpoints. The cloned
`kernelci-pipeline/` checkout and legacy scripts outside this Helm workspace
are not migrated by these changes.
