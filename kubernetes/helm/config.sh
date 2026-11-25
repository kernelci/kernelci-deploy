#!/usr/bin/env bash
# Shared configuration loading for deployment and image-update commands.

DEPLOYMENT_SECRET_NAMES=(API_SECRET_KEY EMAIL_PASSWORD API_TOKEN MONGO KCIDB_REST LAVA_KEY)

unexport_deployment_secrets() {
    # Retain credentials for shell expansion without passing them to child processes.
    export -n "${DEPLOYMENT_SECRET_NAMES[@]}"
}

clear_deployment_secrets() {
    unset "${DEPLOYMENT_SECRET_NAMES[@]}"
}

config_error() { echo "Configuration error: $*" >&2; return 1; }

check_yaml_mapping() {
    [[ -f "$1" ]] || { config_error "File not found: $1"; return 1; }
    yq eval-all -e '[.] | ((length == 1) and ((.[0] | tag) == "!!map"))' "$1" >/dev/null || {
        config_error "Expected one YAML mapping in $1"; return 1;
    }
}

load_deployment_config() {
    command -v yq >/dev/null 2>&1 && [[ "$(yq --version)" == *'version v4.'* ]] || {
        config_error 'Mike Farah yq v4 is required'; return 1;
    }
    DEPLOYMENT="${DEPLOYMENT:-kernelci-org}"
    ENVIRONMENT="${ENVIRONMENT:-prod}"
    [[ "$DEPLOYMENT" =~ ^[a-zA-Z0-9_-]+$ && "$ENVIRONMENT" =~ ^[a-zA-Z0-9_-]+$ ]] || {
        config_error 'Invalid DEPLOYMENT or ENVIRONMENT selector'; return 1;
    }
    export DEPLOYMENT ENVIRONMENT
    VALUES_DIR="${SCRIPT_DIR}/environments/${DEPLOYMENT}/${ENVIRONMENT}"
    SECRETS_DIR="${VALUES_DIR}/secrets"
    DEPLOYMENT_FILE="${VALUES_DIR}/deployment.yaml"
    API_VALUES_FILE="${VALUES_DIR}/api-values.yaml"
    PIPELINE_VALUES_FILE="${VALUES_DIR}/pipeline-values.yaml"
    local file fields name value
    for file in "$DEPLOYMENT_FILE" "$API_VALUES_FILE" "$PIPELINE_VALUES_FILE"; do
        check_yaml_mapping "$file" || return 1
    done
    [[ "$(yq eval '.envoyGateway.values | tag' "$DEPLOYMENT_FILE")" == '!!map' ]] || {
        config_error 'envoyGateway.values must be a YAML mapping'; return 1;
    }
    fields="$(yq eval -r '{
        "CONTEXT": .kubernetes.context,
        "NS_API": .kubernetes.namespaces.api,
        "NS_PIPELINE": .kubernetes.namespaces.pipeline,
        "AZURE_RG": .azure.resourceGroup,
        "CLUSTER_NAME": .azure.clusterName,
        "LOCATION": .azure.location,
        "ENVOY_GATEWAY_CHART": .envoyGateway.chart,
        "ENVOY_GATEWAY_VERSION": .envoyGateway.version,
        "ENVOY_GATEWAY_NAMESPACE": .envoyGateway.namespace,
        "INSTALL_CERT_MANAGER": .certManager.install,
        "CORE_REPO_URL": .sources.core.repository,
        "CORE_BRANCH": .sources.core.branch,
        "API_REPO_URL": .sources.api.repository,
        "API_BRANCH": .sources.api.branch,
        "PIPELINE_REPO_URL": .sources.pipeline.repository,
        "PIPELINE_BRANCH": .sources.pipeline.branch,
        "IMAGE_PREFIX": .build.imagePrefix,
        "UPDATE_TIMEOUT": .update.timeout
    } | to_entries | .[] | [.key, .value] | @tsv' "$DEPLOYMENT_FILE")" || return 1
    while IFS=$'\t' read -r name value; do
        [[ -n "$value" && "$value" != null && "$value" != *'\n'* && "$value" != *'\t'* ]] || {
            config_error "Missing or invalid $name in $DEPLOYMENT_FILE"; return 1;
        }
        printf -v "$name" '%s' "$value"
    done <<< "$fields"
    [[ "$NS_API" != "$NS_PIPELINE" ]] || { config_error 'API and Pipeline namespaces must differ'; return 1; }
    [[ "$INSTALL_CERT_MANAGER" == true || "$INSTALL_CERT_MANAGER" == false ]] || {
        config_error 'certManager.install must be true or false'; return 1;
    }
    [[ "$UPDATE_TIMEOUT" =~ ^[0-9]+(ms|s|m|h)([0-9]+(ms|s|m|h))*$ ]] || {
        config_error 'update.timeout must be a duration such as 10m'; return 1;
    }
    [[ "$IMAGE_PREFIX" == */ ]] || { config_error 'build.imagePrefix must end with /'; return 1; }
    validate_pipeline_includes || return 1
}

validate_pipeline_includes() {
    local index count directory entry
    [[ "$(yq eval '.pipelineConfig.include | tag' "$DEPLOYMENT_FILE")" == '!!seq' ]] || {
        config_error 'pipelineConfig.include must be a nonempty list'; return 1;
    }
    count="$(yq eval '.pipelineConfig.include | length' "$DEPLOYMENT_FILE")"
    (( count > 0 )) || { config_error 'pipelineConfig.include must not be empty'; return 1; }
    for ((index=0; index<count; index++)); do
        entry=".pipelineConfig.include[$index]"
        [[ "$(yq eval "$entry | tag" "$DEPLOYMENT_FILE")" == '!!map' &&
           "$(yq eval "$entry.directory | tag" "$DEPLOYMENT_FILE")" == '!!str' ]] || {
            config_error "$entry must specify a directory string"; return 1;
        }
        directory="$(yq eval -r "$entry.directory" "$DEPLOYMENT_FILE")"
        [[ -n "$directory" && "$directory" != *$'\n'* && "$directory" != *$'\t'* && "$directory" != *$'\r'* ]] || {
            config_error "$entry.directory must be a nonempty single-line path"; return 1;
        }
        if [[ "$(yq eval "$entry.files | tag" "$DEPLOYMENT_FILE")" == '!!str' &&
              "$(yq eval -r "$entry.files" "$DEPLOYMENT_FILE")" == all ]]; then
            continue
        fi
        yq eval -e "$entry.files | ((tag == \"!!seq\") and (length > 0) and
            ([ .[] | select(tag != \"!!str\" or . == \"\" or test(\"[\\\\n\\\\r\\\\t]\")) ] | length == 0))" \
            "$DEPLOYMENT_FILE" >/dev/null || {
            config_error "$entry.files must be 'all' or a nonempty list of file paths"; return 1;
        }
    done
}

pipeline_config_directory() {
    # Only repository-relative paths follow the updater's fresh checkout.
    case "$1" in
        kernelci-pipeline) printf '%s\n' "${PIPELINE_CHECKOUT_DIR:-${SCRIPT_DIR}/kernelci-pipeline}" ;;
        kernelci-pipeline/*) printf '%s/%s\n' "${PIPELINE_CHECKOUT_DIR:-${SCRIPT_DIR}/kernelci-pipeline}" "${1#kernelci-pipeline/}" ;;
        /*|[A-Za-z]:/*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$SCRIPT_DIR" "$1" ;;
    esac
}

load_chart_values() {
    # Deep-merge maps and replace arrays, matching the chart fields read below.
    API_VALUES_JSON="$(yq eval-all -o=json -I=0 '. as $v ireduce ({}; . * $v)' \
        "${SCRIPT_DIR}/charts/api/values.yaml" "$API_VALUES_FILE")" || return 1
    PIPELINE_VALUES_JSON="$(yq eval-all -o=json -I=0 '. as $v ireduce ({}; . * $v)' \
        "${SCRIPT_DIR}/charts/pipeline/values.yaml" "$PIPELINE_VALUES_FILE")" || return 1
}

chart_value() {
    local values
    case "$1" in api) values="$API_VALUES_JSON" ;; pipeline) values="$PIPELINE_VALUES_JSON" ;; esac
    printf '%s\n' "$values" | yq eval -r "$2 | select(. != null)" -
}

load_secrets() {
    # Source the shell-format credentials in isolation: legacy configuration
    # assignments cannot override the selected environment or YAML settings.
    local names=("${DEPLOYMENT_SECRET_NAMES[@]}")
    local values=() index name
    clear_deployment_secrets
    local credentials_file="${SECRETS_DIR}/deploy.cfg"
    [[ -f "$credentials_file" && -r "$credentials_file" ]] || {
        config_error "Credentials required for ${DEPLOYMENT}/${ENVIRONMENT}: $credentials_file"; return 1;
    }
    mapfile -d '' -t values < <(
        set -e
        source "$credentials_file" >/dev/null 2>&1
        for name in "${names[@]}"; do printf '%s\0' "${!name:-}"; done
    )
    [[ "${#values[@]}" == "${#names[@]}" ]] || { config_error "Could not load $credentials_file for ${DEPLOYMENT}/${ENVIRONMENT}"; return 1; }
    for index in "${!names[@]}"; do printf -v "${names[index]}" '%s' "${values[index]}"; done
}
