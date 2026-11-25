#!/usr/bin/env bash
set -euo pipefail

# Helm-based KernelCI deployment script.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"

clear_deployment_secrets
umask 077
unset mongo_uri
trap 'clear_deployment_secrets; unset mongo_uri' EXIT

usage() {
    cat <<EOF
Usage: $0 COMMAND

Commands:
  show-deployments           Test cluster access and list API/Pipeline deployments
  full                       Deploy everything
  apply                      Apply environment values to both releases without provisioning infrastructure
  update                     Update installed releases using image digests and fresh Pipeline config
  delete-all                 Delete the API, Pipeline and Envoy namespaces
  token                      Update tokens, credentials, and Pipeline config
  config                     Update Pipeline config (optional directory argument)
  pipeline-configmap         Update Pipeline config
  backup-config              Save the live Pipeline ConfigMap locally
  pipeline-credentials       Update all API/Pipeline credentials
  deploy_secret_toml_only    Update only kernelci-secrets.toml
  retrieve_secrets_toml      Retrieve kernelci-secrets.toml
  retrieve_k8s_credentials   Retrieve the build-cluster credentials archive
  update_k8s_credentials     Install k8s-new.tgz as build-cluster credentials
  cert                       Install or update the cert-manager ClusterIssuer
  backup-mongo               Back up an in-cluster MongoDB pod
  restore-mongo              Report the legacy unimplemented restore operation
  mongo-shell                Open mongosh in the in-cluster MongoDB pod
  api-restart-pods           Restart the API deployment
  pipeline-restart-pods      Restart all Pipeline deployments

Select DEPLOYMENT and ENVIRONMENT (defaults: kernelci-org and prod).
Credential writes require both selectors to be set explicitly.
Configuration comes from environments/DEPLOYMENT/ENVIRONMENT/*.yaml.
Credentials are read only from the selected environment secrets/ directory.
EOF
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || { echo "Required command not found: $1" >&2; exit 1; }
}

check_context() {
    kubectl config get-contexts -o name | grep -Fxq "${CONTEXT}" || {
        echo "Kubernetes context not found: ${CONTEXT}" >&2
        exit 1
    }
    echo "Kubernetes context found: ${CONTEXT}"
}

check_deployment_files() {
    local chart values_file
    for chart in api pipeline; do
        values_file="${SCRIPT_DIR}/environments/${DEPLOYMENT}/${ENVIRONMENT}/${chart}-values.yaml"
        [[ -f "${values_file}" ]] || {
            echo "Environment values not found: ${values_file}" >&2
            exit 1
        }
    done
    echo "Deployment and environment values found: ${DEPLOYMENT}-${ENVIRONMENT}"
}

validate_secret_inputs() {
    local mongo_key kcidb_key name file
    local required=(API_SECRET_KEY EMAIL_PASSWORD API_TOKEN)
    mongo_key="$(chart_value api '.config.mongoServiceSecretKey')"
    kcidb_key="$(chart_value pipeline '.environment.kcidbRestSecretKey')"
    if [[ "$(chart_value api '.mongo.enabled')" != true && -n "$mongo_key" ]]; then
        required+=(MONGO)
    fi
    if [[ -n "$kcidb_key" ]]; then
        required+=(KCIDB_REST)
    fi
    for name in "${required[@]}"; do
        [[ -n "${!name:-}" ]] || { config_error "Missing $name for ${DEPLOYMENT}/${ENVIRONMENT} in ${SECRETS_DIR}/deploy.cfg"; return 1; }
    done
    for file in kernelci-secrets.toml k8s.tgz; do
        [[ -f "${SECRETS_DIR}/${file}" && -r "${SECRETS_DIR}/${file}" ]] || {
            config_error "Credential file required for ${DEPLOYMENT}/${ENVIRONMENT}: ${SECRETS_DIR}/${file}"; return 1;
        }
    done
}

apply_secrets() {
    validate_secret_inputs
    local mongo_key kcidb_key
    local api_secret_args=() pipeline_secret_args=()
    mongo_key="$(chart_value api '.config.mongoServiceSecretKey')"
    kcidb_key="$(chart_value pipeline '.environment.kcidbRestSecretKey')"
    api_secret_args=(
        --from-literal="secret-key=${API_SECRET_KEY}"
        --from-literal="email-password=${EMAIL_PASSWORD}"
    )
    echo "Deploying kernelci-pipeline secrets..."
    echo "Setting secrets...(API token, k8s, toml)"

    kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" create secret generic "${API_TOKEN_SECRET}" \
        --from-literal=token="${API_TOKEN}" --dry-run=client -o yaml | \
        kubectl --context "${CONTEXT}" apply -f -

    if [[ "$(chart_value api '.mongo.enabled')" != true && -n "$mongo_key" ]]; then
        api_secret_args+=(--from-literal="${mongo_key}=${MONGO}")
    fi
    echo "Setting API credentials"
    kubectl --context "${CONTEXT}" -n "${NS_API}" create secret generic "${API_SECRET}" \
        "${api_secret_args[@]}" --dry-run=client -o yaml | \
        kubectl --context "${CONTEXT}" apply -f -

    echo "Setting kernelci-secrets.toml..."
    [[ -f "${SECRETS_DIR}/kernelci-secrets.toml" ]] || { echo "kernelci-secrets.toml not found" >&2; exit 1; }
    pipeline_secret_args=(--from-file="kernelci.toml=${SECRETS_DIR}/kernelci-secrets.toml")
    if [[ -n "$kcidb_key" ]]; then
        pipeline_secret_args+=(--from-literal="${kcidb_key}=${KCIDB_REST}")
    fi
    [[ -f "${SECRETS_DIR}/id_rsa" ]] && echo "Setting id_rsa... this part not tested yet" && pipeline_secret_args+=(--from-file="id_rsa=${SECRETS_DIR}/id_rsa")
    [[ -f "${SECRETS_DIR}/kcidb-credentials.json" ]] && echo "Setting kcidb-credentials... this part not tested yet" && pipeline_secret_args+=(--from-file="kcidb-credentials.json=${SECRETS_DIR}/kcidb-credentials.json")
    kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" create secret generic "${PIPELINE_SECRET}" \
        "${pipeline_secret_args[@]}" --dry-run=client -o yaml | \
        kubectl --context "${CONTEXT}" apply -f -

    echo "Setting k8s credentials..."
    [[ -f "${SECRETS_DIR}/k8s.tgz" ]] || { echo "k8s.tgz not found" >&2; exit 1; }
    kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" create secret generic "${KUBERNETES_SECRET}" \
        --from-file="k8s-credentials.tgz=${SECRETS_DIR}/k8s.tgz" --dry-run=client -o yaml | \
        kubectl --context "${CONTEXT}" apply -f -
    clear_deployment_secrets
}

apply_pipeline_toml() {
    local encoded patch
    export -n encoded patch
    [[ -f "${SECRETS_DIR}/kernelci-secrets.toml" ]] || { echo "kernelci-secrets.toml not found" >&2; exit 1; }
    encoded="$(base64 -w 0 "${SECRETS_DIR}/kernelci-secrets.toml")"
    patch="$(printf '{"data":{"kernelci.toml":"%s"}}' "${encoded}")"
    kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" patch secret "${PIPELINE_SECRET}" --type merge -p "${patch}"
}

apply_k8s_credentials() {
    local archive="${1}"
    [[ -f "${archive}" ]] || { echo "Credentials archive not found: ${archive}" >&2; exit 1; }
    kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" create secret generic "${KUBERNETES_SECRET}" \
        --from-file="k8s-credentials.tgz=${archive}" --dry-run=client -o yaml | \
        kubectl --context "${CONTEXT}" apply -f -
}

assign_ips_and_dns() {
    local component dns ip resource_id internal group values_file
    local annotations='.gateway.serviceAnnotations."service.beta.kubernetes.io/'
    for component in api pipeline; do
        [[ "$(chart_value "$component" '.gateway.enabled')" == true ]] || continue
        internal="$(chart_value "$component" "${annotations}azure-load-balancer-internal\"")"
        [[ "$internal" != true ]] || continue
        dns="$(chart_value "$component" "${annotations}azure-dns-label-name\"")"
        # Without a DNS label, let the Azure LoadBalancer manage its address.
        [[ -n "$dns" ]] || continue
        group="$(chart_value "$component" "${annotations}azure-load-balancer-resource-group\"")"
        group="${group:-$AZURE_RG}"
        ip="$(chart_value "$component" "${annotations}azure-load-balancer-ipv4\"")"
        if [[ -z "${ip}" ]]; then
            ip="$(az network public-ip show --resource-group "$group" --name "${dns}-ip" --query ipAddress -o tsv 2>/dev/null || true)"
            if [[ -z "${ip}" ]]; then
                ip="$(az network public-ip create --resource-group "$group" --name "${dns}-ip" --sku Standard --allocation-method static --location "${LOCATION}" --query publicIp.ipAddress -o tsv)"
            fi
            [[ -n "${ip}" ]] || { echo "Failed to allocate ${component} public IP" >&2; exit 1; }
            if [[ "$component" == api ]]; then values_file="$API_VALUES_FILE"; else values_file="$PIPELINE_VALUES_FILE"; fi
            DISCOVERED_IP="$ip" yq eval -i '.gateway.serviceAnnotations."service.beta.kubernetes.io/azure-load-balancer-ipv4" = strenv(DISCOVERED_IP)' "$values_file"
        fi
        echo "Waiting for IP to propagate, 10 seconds..."
        sleep 10
        resource_id="$(az network public-ip list --resource-group "$group" --query "[?ipAddress=='${ip}'].id | [0]" -o tsv)"
        [[ -n "${resource_id}" ]] || { echo "Public IP not found in $group: ${ip}" >&2; exit 1; }
        az network public-ip update --ids "${resource_id}" --dns-name "${dns}" >/dev/null
    done
    load_chart_values
}

build_network_args() {
    # Populate api_network_args and pipeline_network_args for the caller.
    local component group
    local prefix='gateway.serviceAnnotations.service\.beta\.kubernetes\.io/'
    local args=()
    api_network_args=()
    pipeline_network_args=()
    for component in api pipeline; do
        group="$(chart_value "$component" '.gateway.serviceAnnotations."service.beta.kubernetes.io/azure-load-balancer-resource-group"')"
        args=()
        # The deployment resource group supplies the default without duplicating it.
        [[ -n "$group" || -z "$AZURE_RG" ]] || args+=(--set-string "${prefix}azure-load-balancer-resource-group=${AZURE_RG}")
        if [[ "${component}" == api ]]; then api_network_args=("${args[@]}"); else pipeline_network_args=("${args[@]}"); fi
    done
}

grant_azure_network_access() {
    [[ -n "${AZURE_RG}" ]] || return 0
    echo "Assign permissions for cluster networking"
    echo "Retrieving cluster principal id..."
    local client_id rg_scope subnet_scope
    client_id="$(az aks show --name "${CLUSTER_NAME}" --resource-group "${AZURE_RG}" --query identity.principalId -o tsv)"

    echo "Retrieving cluster resource group scope..."
    rg_scope="$(az group show --name "${AZURE_RG}" --query id -o tsv)"

    echo "Retrieving agent subnet scope (custom VNet support)..."
    subnet_scope="$(az aks show --name "${CLUSTER_NAME}" --resource-group "${AZURE_RG}" --query 'agentPoolProfiles[0].vnetSubnetId' -o tsv)"

    echo "Assigning Network Contributor on ${rg_scope} (IP resources)..."
    az role assignment create --assignee "${client_id}" --role "Network Contributor" --scope "${rg_scope}" >/dev/null || true

    if [[ -n "$subnet_scope" ]]; then
        echo "Assigning Network Contributor on ${subnet_scope} (subnet for internal LoadBalancer)..."
        az role assignment create --assignee "${client_id}" --role "Network Contributor" --scope "${subnet_scope}" || true
    fi
}

install_cluster_services() {
    local envoy_values
    export -n envoy_values
    envoy_values="$(yq eval '.envoyGateway.values' "$DEPLOYMENT_FILE")" || return 1
    helm upgrade --install kernelci-envoy "${ENVOY_GATEWAY_CHART}" \
        --version "${ENVOY_GATEWAY_VERSION}" --kube-context "${CONTEXT}" \
        --namespace "${ENVOY_GATEWAY_NAMESPACE}" --create-namespace \
        --reset-values -f - \
        --wait --timeout 10m <<< "$envoy_values"
    kubectl --context "${CONTEXT}" apply -f "${SCRIPT_DIR}/manifests/envoy/gatewayclass.yaml"
    if [[ "${INSTALL_CERT_MANAGER}" == true ]]; then
        helm repo add jetstack https://charts.jetstack.io >/dev/null
        helm repo update jetstack >/dev/null
        helm upgrade --install cert-manager jetstack/cert-manager --kube-context "${CONTEXT}" \
            --namespace cert-manager --create-namespace --set crds.enabled=true \
            --set config.gatewayAPI.enabled=true --wait --timeout 10m
    fi
}

build_issuer_args() {
    # Populate issuer_args for the caller.
    issuer_args=(
        --set-string "issuer.apiGatewayNamespace=${NS_API}"
        --set-string "issuer.pipelineGatewayNamespace=${NS_PIPELINE}"
        --set-json "issuer.pipelineHostnames=$(chart_value pipeline '.gateway.hostnames | to_json')"
    )
}

prepare_charts() {
    # Populate networking, issuer, API-wait and Pipeline-file argument arrays.
    build_network_args
    build_issuer_args
    pipeline_wait_args=()
    # An explicit environment URL wins; otherwise derive it from the target namespace.
    if [[ -z "$(yq eval -r '.waitForApi.url | select(. != null)' "$PIPELINE_VALUES_FILE")" ]]; then
        pipeline_wait_args=(--set-string "waitForApi.url=http://api.${NS_API}.svc.cluster.local/")
    fi
    pipeline_file_args=()
    if [[ -z "$(chart_value pipeline '.pipelineConfig.existingConfigMap')" ]]; then
        build_pipeline_file_args "${1:-}"
    fi
}

validate_charts() {
    helm template kernelci-api "${SCRIPT_DIR}/charts/api" --namespace "$NS_API" \
        -f "$API_VALUES_FILE" "${api_network_args[@]}" "${issuer_args[@]}" >/dev/null
    helm template kernelci-pipeline "${SCRIPT_DIR}/charts/pipeline" --namespace "$NS_PIPELINE" \
        -f "$PIPELINE_VALUES_FILE" "${pipeline_wait_args[@]}" "${pipeline_file_args[@]}" \
        "${pipeline_network_args[@]}" >/dev/null
}

deploy_charts() {
    prepare_charts "${1:-}"
    validate_charts
    helm upgrade --install kernelci-api "${SCRIPT_DIR}/charts/api" \
        --kube-context "${CONTEXT}" --namespace "${NS_API}" \
        --reset-values -f "$API_VALUES_FILE" "${api_network_args[@]}" "${issuer_args[@]}" \
        --wait --timeout "$UPDATE_TIMEOUT"
    helm upgrade --install kernelci-pipeline "${SCRIPT_DIR}/charts/pipeline" \
        --kube-context "${CONTEXT}" --namespace "${NS_PIPELINE}" \
        --reset-values -f "$PIPELINE_VALUES_FILE" "${pipeline_wait_args[@]}" \
        "${pipeline_file_args[@]}" "${pipeline_network_args[@]}" --wait --timeout "$UPDATE_TIMEOUT"
}

show_deployment_urls() {
    local component hostname
    echo "----------------------------------------"
    echo "Done"
    for component in api pipeline; do
        [[ "$(chart_value "$component" '.gateway.enabled')" == true ]] || continue
        while IFS= read -r hostname; do
            if [[ "$component" == api ]]; then
                echo "Test API availability by curl https://${hostname}/latest/"
            else
                echo "Test lava callback availability by curl https://${hostname}/"
            fi
        done < <(chart_value "$component" '.gateway.hostnames[]')
    done
}

build_pipeline_file_args() {
    # Populate pipeline_file_args in the caller's scope (including a local array).
    local override="${1:-}" config_dir selection source source_count file_count file_index
    local config_file entry name previous_name index=0
    local config_files=() names=()
    source_count="$(yq eval '.pipelineConfig.include | length' "$DEPLOYMENT_FILE")"
    if [[ -n "$override" && "$source_count" != 1 ]]; then
        echo 'A configuration-directory argument requires exactly one pipelineConfig.include entry' >&2
        return 1
    fi
    for ((source=0; source<source_count; source++)); do
        selection=".pipelineConfig.include[$source]"
        config_dir="$(pipeline_config_directory "$(yq eval -r "$selection.directory" "$DEPLOYMENT_FILE")")"
        config_dir="${override:-$config_dir}"
        [[ -d "$config_dir" && -r "$config_dir" ]] || {
            echo "Pipeline config directory not found or unreadable: $config_dir" >&2; return 1;
        }
        if [[ "$(yq eval -r "$selection.files" "$DEPLOYMENT_FILE")" == all ]]; then
            file_count="${#config_files[@]}"
            while IFS= read -r -d '' config_file; do
                config_files+=("$config_file")
            done < <(find "$config_dir" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 | LC_ALL=C sort -z)
            (( ${#config_files[@]} > file_count )) || {
                echo "No YAML configuration files found in $config_dir" >&2; return 1;
            }
            continue
        fi
        file_count="$(yq eval "$selection.files | length" "$DEPLOYMENT_FILE")"
        for ((file_index=0; file_index<file_count; file_index++)); do
            entry="$(yq eval -r "$selection.files[$file_index]" "$DEPLOYMENT_FILE")"
            case "${entry}" in
                /*|[A-Za-z]:*|..|../*|*/../*|*/..)
                    echo "Pipeline config paths must be relative to ${config_dir}: ${entry}" >&2
                    return 1
                    ;;
            esac
            case "${entry}" in
                *.yaml|*.yml) ;;
                *) echo "Pipeline config file must be YAML: ${entry}" >&2; return 1 ;;
            esac
            config_file="${config_dir}/${entry}"
            [[ -f "${config_file}" && -r "${config_file}" ]] || {
                echo "Pipeline config file not found or unreadable: ${config_file} (listed in ${DEPLOYMENT_FILE})" >&2
                return 1
            }
            config_files+=("${config_file}")
        done
    done

    # Replace the entire list even when --reuse-values retained a longer list.
    pipeline_file_args=(--set-json 'pipelineConfig.files=[]')
    for config_file in "${config_files[@]}"; do
        name="$(basename "${config_file}")"
        [[ -r "$config_file" && "$name" =~ ^[a-zA-Z0-9._-]+$ ]] || {
            echo "Unreadable Pipeline config file or invalid ConfigMap filename: $config_file" >&2; return 1;
        }
        for previous_name in "${names[@]}"; do
            [[ "${name}" != "${previous_name}" ]] || {
                echo "Duplicate Pipeline config filename: ${name} (including ${config_file})" >&2
                return 1
            }
        done
        names+=("${name}")
        pipeline_file_args+=(--set-string "pipelineConfig.files[${index}].name=${name}")
        pipeline_file_args+=(--set-file "pipelineConfig.files[${index}].content=${config_file//,/\\,}")
        index=$((index + 1))
    done
    (( index > 0 )) || { echo 'No YAML configuration files selected' >&2; return 1; }
}

deploy_pipeline_config() {
    local config_dir="${1:-}"
    build_pipeline_file_args "${config_dir}"
    helm upgrade --install kernelci-pipeline "${SCRIPT_DIR}/charts/pipeline" \
        --kube-context "${CONTEXT}" --namespace "${NS_PIPELINE}" \
        --reuse-values "${pipeline_file_args[@]}" --wait --timeout 10m
}

save_image_values() (
    # Stage both files before replacing either, so YAML errors leave them intact.
    local values_dir="${SCRIPT_DIR}/environments/${DEPLOYMENT}/${ENVIRONMENT}"
    local work_dir component pipeline_expression
    export UPDATE_IMAGE_REPOSITORY="$1"
    export API_IMAGE_DIGEST PIPELINE_IMAGE_DIGEST LAVA_CALLBACK_IMAGE_DIGEST
    shift
    work_dir="$(mktemp -d "${values_dir}/.image-pins.XXXXXX")"
    trap 'rm -rf -- "${work_dir}"' EXIT
    cp -p "${values_dir}/api-values.yaml" "${work_dir}/api-values.yaml"
    cp -p "${values_dir}/pipeline-values.yaml" "${work_dir}/pipeline-values.yaml"

    yq eval -i '
        .image.repository = strenv(UPDATE_IMAGE_REPOSITORY) |
        .image.tag = ("api@" + strenv(API_IMAGE_DIGEST))
    ' "${work_dir}/api-values.yaml"

    pipeline_expression='
        .credentialsInit.image.repository = strenv(UPDATE_IMAGE_REPOSITORY) |
        .credentialsInit.image.tag = ("pipeline@" + strenv(PIPELINE_IMAGE_DIGEST)) |
        .components."lava-callback".image.repository = strenv(UPDATE_IMAGE_REPOSITORY) |
        .components."lava-callback".image.tag = ("lava-callback@" + strenv(LAVA_CALLBACK_IMAGE_DIGEST))
    '
    for component in "$@"; do
        pipeline_expression+=" | .components.\"${component}\".image.repository = strenv(UPDATE_IMAGE_REPOSITORY)"
        pipeline_expression+=" | .components.\"${component}\".image.tag = (\"pipeline@\" + strenv(PIPELINE_IMAGE_DIGEST))"
    done
    yq eval -i "${pipeline_expression}" "${work_dir}/pipeline-values.yaml"

    mv -f -- "${work_dir}/api-values.yaml" "${values_dir}/api-values.yaml"
    mv -f -- "${work_dir}/pipeline-values.yaml" "${values_dir}/pipeline-values.yaml"
    echo "[UPDATE] Saved image pins in ${values_dir}/{api,pipeline}-values.yaml"
)

update_releases() {
    # Update existing releases only: retain their config, Secrets and networking.
    local config_dir="${1:-}"
    local repository="${UPDATE_IMAGE_REPOSITORY:-${IMAGE_PREFIX}kernelci}"
    local timeout="${UPDATE_TIMEOUT:-10m}"
    local digest component trigger_replicas
    local pipeline_file_args=() image_args=()
    local pipeline_components=(timeout closing holdoff scheduler-k8s scheduler-shell scheduler-lava tarball trigger pipeline-kcidb)
    require_command python3
    require_command yq
    [[ "$(yq --version)" == *'version v4.'* ]] || { echo "Mike Farah yq v4 is required" >&2; return 1; }
    for digest in "${API_IMAGE_DIGEST:-}" "${PIPELINE_IMAGE_DIGEST:-}" "${LAVA_CALLBACK_IMAGE_DIGEST:-}"; do
        [[ "${digest}" =~ ^sha256:[a-f0-9]{64}$ ]] || {
            echo "Set API_IMAGE_DIGEST, PIPELINE_IMAGE_DIGEST and LAVA_CALLBACK_IMAGE_DIGEST to full sha256 digests" >&2
            return 1
        }
    done
    [[ "${NS_API}" != "${NS_PIPELINE}" ]] || { echo "API and Pipeline namespaces must differ" >&2; return 1; }
    helm status kernelci-api --kube-context "${CONTEXT}" -n "${NS_API}" >/dev/null
    helm status kernelci-pipeline --kube-context "${CONTEXT}" -n "${NS_PIPELINE}" >/dev/null
    build_pipeline_file_args "${config_dir}"
    trigger_replicas="$(helm get values kernelci-pipeline --all --kube-context "${CONTEXT}" -n "${NS_PIPELINE}" -o json | python3 -c '
import json, sys
values = json.load(sys.stdin)
# A failed update may have left the trigger paused; retain its original count.
replicas = values.get("updateTriggerReplicas")
if replicas is None:
    replicas = values.get("components", {}).get("trigger", {}).get("replicas", 1)
if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0:
    sys.exit("Invalid trigger replica count")
print(replicas)
')"

    save_image_values "${repository}" "${pipeline_components[@]}"

    echo "[UPDATE] Updating API in ${CONTEXT}/${NS_API}"
    helm upgrade kernelci-api "${SCRIPT_DIR}/charts/api" \
        --kube-context "${CONTEXT}" --namespace "${NS_API}" --reuse-values \
        --set-string "image.repository=${repository}" \
        --set-string "image.tag=api@${API_IMAGE_DIGEST}" --wait --timeout "${timeout}"

    for component in "${pipeline_components[@]}"; do
        image_args+=(--set-string "components.${component}.image.repository=${repository}")
        image_args+=(--set-string "components.${component}.image.tag=pipeline@${PIPELINE_IMAGE_DIGEST}")
    done
    image_args+=(
        --set-string "components.lava-callback.image.repository=${repository}"
        --set-string "components.lava-callback.image.tag=lava-callback@${LAVA_CALLBACK_IMAGE_DIGEST}"
        --set-string "credentialsInit.image.repository=${repository}"
        --set-string "credentialsInit.image.tag=pipeline@${PIPELINE_IMAGE_DIGEST}"
    )
    echo "[UPDATE] Updating Pipeline with the trigger paused"
    if ! helm upgrade kernelci-pipeline "${SCRIPT_DIR}/charts/pipeline" \
        --kube-context "${CONTEXT}" --namespace "${NS_PIPELINE}" --reuse-values \
        --set-string 'pipelineConfig.existingConfigMap=' \
        "${pipeline_file_args[@]}" "${image_args[@]}" \
        --set "updateTriggerReplicas=${trigger_replicas}" --set components.trigger.replicas=0 \
        --wait --timeout "${timeout}"; then
        echo "Pipeline upgrade failed; trigger may be paused. Fix the failure and rerun the update to resume it." >&2
        return 1
    fi
    echo "[UPDATE] Pipeline ready; restoring trigger replicas to ${trigger_replicas}"
    helm upgrade kernelci-pipeline "${SCRIPT_DIR}/charts/pipeline" \
        --kube-context "${CONTEXT}" --namespace "${NS_PIPELINE}" --reuse-values \
        --set "components.trigger.replicas=${trigger_replicas}" --set updateTriggerReplicas=null \
        --wait --timeout "${timeout}"
}

update_pipeline_environment() {
    helm upgrade kernelci-pipeline "${SCRIPT_DIR}/charts/pipeline" \
        --kube-context "${CONTEXT}" --namespace "${NS_PIPELINE}" --reuse-values \
        --set-json "environment=$(chart_value pipeline '.environment | to_json')"
}

show_deployments() {
    local namespace
    echo "Getting deployments for ${CONTEXT} context..."
    for namespace in "${NS_API}" "${NS_PIPELINE}"; do
        echo "Deployments in ${namespace}:"
        kubectl --context "${CONTEXT}" -n "${namespace}" get deployments
    done
}

mongo_pod() {
    kubectl --context "${CONTEXT}" -n "${NS_API}" get pods -l app=mongo -o jsonpath='{.items[0].metadata.name}'
}

mongo_connection_uri() {
    # Resolve the live Deployment's reference without requiring a running API pod.
    local reference kind name key uri resource
    export -n uri
    reference="$(kubectl --context "$CONTEXT" -n "$NS_API" get deployment api -o json |
        yq eval -r '.spec.template.spec.containers[] | select(.name == "api").env[] |
            select(.name == "MONGO_SERVICE").valueFrom | to_entries | .[] |
            [.key, .value.name, .value.key] | @tsv' -)"
    IFS=$'\t' read -r kind name key <<< "$reference"
    [[ -n "$name" && -n "$key" ]] || { echo 'MongoDB reference missing from API deployment' >&2; return 1; }
    case "$kind" in
        secretKeyRef) resource=secret ;;
        configMapKeyRef) resource=configmap ;;
        *) echo 'Unsupported MongoDB reference in API deployment' >&2; return 1 ;;
    esac
    uri="$(kubectl --context "$CONTEXT" -n "$NS_API" get "$resource" "$name" -o json |
        MONGO_DATA_KEY="$key" yq eval -r '.data[strenv(MONGO_DATA_KEY)] | select(. != null)' -)"
    if [[ "$resource" == secret ]]; then printf '%s' "$uri" | base64 --decode; else printf '%s' "$uri"; fi
}

clone_repos() {
    local directory
    while IFS= read -r directory; do
        case "$directory" in
            kernelci-pipeline|kernelci-pipeline/*)
                if [[ ! -d "${SCRIPT_DIR}/kernelci-pipeline" ]]; then
                    echo "kernelci-pipeline not found, cloning branch ${PIPELINE_BRANCH}..."
                    git clone "$PIPELINE_REPO_URL" -b "${PIPELINE_BRANCH}" "${SCRIPT_DIR}/kernelci-pipeline"
                fi
                ;;
        esac
    done < <(yq eval -r '.pipelineConfig.include[].directory' "$DEPLOYMENT_FILE")
}

# CLI entry point
case "${1:-full}" in -h|--help) usage; exit 0 ;; esac
# Require an intentional target before configuration defaults are applied.
case "${1:-full}" in
    full|token|pipeline-credentials|deploy_secret_toml_only|update_k8s_credentials)
        [[ -n "${DEPLOYMENT:-}" && -n "${ENVIRONMENT:-}" ]] || {
            config_error 'Set both DEPLOYMENT and ENVIRONMENT explicitly for credential writes'
            exit 1
        }
        ;;
esac
load_deployment_config
load_chart_values
case "${1:-full}" in full|token|pipeline-credentials) load_secrets; validate_secret_inputs ;; esac
API_SECRET="$(chart_value api '.existingSecret')"
API_TOKEN_SECRET="$(chart_value pipeline '.secrets.apiToken')"
PIPELINE_SECRET="$(chart_value pipeline '.secrets.pipeline')"
KUBERNETES_SECRET="$(chart_value pipeline '.secrets.kubernetesCredentials')"
require_command kubectl
require_command helm
check_context
check_deployment_files

case "${1:-usage}" in
    show-deployments)
        show_deployments
        ;;
    full)
        require_command az
        clone_repos
        prepare_charts
        validate_charts
        kubectl --context "${CONTEXT}" create namespace "${NS_API}" --dry-run=client -o yaml | kubectl --context "${CONTEXT}" apply -f -
        kubectl --context "${CONTEXT}" create namespace "${NS_PIPELINE}" --dry-run=client -o yaml | kubectl --context "${CONTEXT}" apply -f -
        assign_ips_and_dns
        grant_azure_network_access
        apply_secrets
        install_cluster_services
        deploy_charts
        show_deployment_urls
        ;;
    apply)
        deploy_charts "${2:-}"
        ;;
    delete-all)
        kubectl --context "${CONTEXT}" delete namespace "${NS_API}" "${NS_PIPELINE}" "${ENVOY_GATEWAY_NAMESPACE}" --ignore-not-found
        ;;
    config)
        deploy_pipeline_config "${2:-}"
        ;;
    update)
        update_releases "${2:-}"
        ;;
    pipeline-configmap)
        deploy_pipeline_config
        ;;
    token)
        apply_secrets
        deploy_pipeline_config
        update_pipeline_environment
        ;;
    pipeline-credentials)
        apply_secrets
        update_pipeline_environment
        ;;
    deploy_secret_toml_only)
        apply_pipeline_toml
        ;;
    retrieve_secrets_toml)
        mkdir -p -- "$SECRETS_DIR"
        kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" get secret "$PIPELINE_SECRET" \
            -o jsonpath='{.data.kernelci\.toml}' | base64 --decode > "${SECRETS_DIR}/kernelci-secrets.toml"
        echo "Retrieved ${SECRETS_DIR}/kernelci-secrets.toml"
        ;;
    retrieve_k8s_credentials)
        mkdir -p -- "$SECRETS_DIR"
        kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" get secret "$KUBERNETES_SECRET" \
            -o jsonpath='{.data.k8s-credentials\.tgz}' | base64 --decode > "${SECRETS_DIR}/k8s-old.tgz"
        echo "Retrieved ${SECRETS_DIR}/k8s-old.tgz"
        ;;
    update_k8s_credentials)
        apply_k8s_credentials "${SECRETS_DIR}/k8s-new.tgz"
        ;;
    backup-config)
        kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" get configmap pipeline-configmap \
            -o yaml > "${SCRIPT_DIR}/pipeline-configmap.yaml"
        echo "Saved ${SCRIPT_DIR}/pipeline-configmap.yaml"
        ;;
    cert)
        build_issuer_args
        helm upgrade kernelci-api "${SCRIPT_DIR}/charts/api" --reuse-values \
            --kube-context "${CONTEXT}" --namespace "${NS_API}" \
            --set-json "issuer=$(chart_value api '.issuer | to_json')" \
            --set issuer.enabled=true "${issuer_args[@]}"
        ;;
    backup-mongo)
        mongo_uri="$(mongo_connection_uri)"
        [[ -n "${mongo_uri}" ]] || { echo "MongoDB URI not found in the API configuration" >&2; exit 1; }
        pod="$(mongo_pod)"
        [[ -n "${pod}" ]] || { echo "MongoDB pod not found in ${NS_API}" >&2; exit 1; }
        kubectl --context "${CONTEXT}" -n "${NS_API}" exec "${pod}" -- rm -f /tmp/backup.gz
        kubectl --context "${CONTEXT}" -n "${NS_API}" exec "${pod}" -- mongodump --uri="${mongo_uri}" --archive=/tmp/backup.gz --gzip
        unset mongo_uri
        backup_file="${SCRIPT_DIR}/mongobackup-$(date +%Y%m%d%H%M%S).gz"
        kubectl --context "${CONTEXT}" -n "${NS_API}" cp --retries=10 "${pod}:/tmp/backup.gz" "${backup_file}"
        echo "Saved ${backup_file}"
        ;;
    restore-mongo)
        echo "Broken at moment, we need to implement restore logic for tarball"
        ;;
    mongo-shell)
        pod="$(mongo_pod)"
        [[ -n "${pod}" ]] || { echo "MongoDB pod not found in ${NS_API}" >&2; exit 1; }
        kubectl --context "${CONTEXT}" -n "${NS_API}" exec -it "${pod}" -- mongosh kernelci
        ;;
    api-restart-pods)
        kubectl --context "${CONTEXT}" -n "${NS_API}" rollout restart deployment/api
        ;;
    pipeline-restart-pods)
        kubectl --context "${CONTEXT}" -n "${NS_PIPELINE}" rollout restart deployment
        ;;
    *) usage; exit 1 ;;
esac
