#!/bin/bash -e
# 
# This script will deploy kernelci API/Pipeline to your cluster
# SPDX-License-Identifier: LGPL-2.1-only
# (c) Collabora Ltd. 2023
# Author: Denys Fedoryshchenko <denys.f@collabora.com>
#
# TODO(nuclearcat): Most of things can be converted to helm charts
# and yq replaced by kustomize
# TODO: Add managed disk pickup or creation for mongo

# Azure specific variables, unset if you are not using Azure
AZURE_RG="kernelci-api-1_group"
LOCATION="westus3"

CONTEXT="kernelci-api-1"
CLUSTER_NAME="kernelci-api-1"

# Pipeline namespace and DNS label
NS_PIPELINE="kernelci-pipeline"
DNS_PIPELINE="kernelci-pipeline"

# API namespace and DNS label
DNS_API="kernelci-api"
NS_API="kernelci-api"

# ACME type - staging or production
# If you are experimenting, use staging, to not hit rate limits
ACME="staging"

# git branch for kernelci-api and pipeline projects, normally it is
# stable snapshot: kernelci.org
BRANCH="main"

# This might contain IP variables
if [ -f deploy.cfg ]; then
    source deploy.cfg
fi

# if ACME staging - ACME URL IS
if [ "$ACME" == "staging" ]; then
    ACME_URL="https://acme-staging-v02.api.letsencrypt.org/directory"
else
    ACME_URL="https://acme-v02.api.letsencrypt.org/directory"
fi


# We need static ip for LAVA callback
function fetch_static_ips {
    # check if public-ip already exist
    echo "Verifying if IP resources already exist..."
    IP_PIPELINE=$(az network public-ip show --resource-group $AZURE_RG --name ${DNS_PIPELINE}-ip --query ipAddress -o tsv || true)
    IP_API=$(az network public-ip show --resource-group $AZURE_RG --name ${DNS_API}-ip --query ipAddress -o tsv || true)

    if [ -z "$IP_PIPELINE" ]; then
        echo "IP_PIPELINE not set, creating..."
        az network public-ip create --resource-group $AZURE_RG --name ${DNS_PIPELINE}-ip --sku Standard --allocation-method static --query publicIp.ipAddress -o tsv --location $LOCATION
        IP_PIPELINE=$(az network public-ip show --resource-group $AZURE_RG --name ${DNS_PIPELINE}-ip --query ipAddress -o tsv)
    fi
    if [ -z "$IP_API" ]; then
        echo "IP_API not set, creating..."
        az network public-ip create --resource-group $AZURE_RG --name ${DNS_API}-ip --sku Standard --allocation-method static --query publicIp.ipAddress -o tsv --location $LOCATION
        IP_API=$(az network public-ip show --resource-group $AZURE_RG --name ${DNS_API}-ip --query ipAddress -o tsv)
    fi

    echo "export IP_API=\"${IP_API}\"" >> deploy.cfg
    echo "export IP_PIPELINE=\"${IP_PIPELINE}\"" >> deploy.cfg
}

function azure_ip_permissions {
    echo "Assign permissions for cluster to read/retrieve IPs"
    echo "Retrieving cluster resource group..."
    RG_SCOPE=$(az group show --name $AZURE_RG --query id -o tsv)
    echo "Retrieving cluster client id..."
    CLIENT_ID=$(az aks show --name $CLUSTER_NAME --resource-group $AZURE_RG --query identity.principalId -o tsv)
    echo "Assigning permissions..."
    az role assignment create \
        --assignee ${CLIENT_ID} \
        --role "Network Contributor" \
        --scope ${RG_SCOPE}
}

function local_setup {
    # obviously, do we have working kubectl?
    if ! command -v kubectl &> /dev/null
    then
        echo "kubectl not found, exiting"
        exit
    fi
    if ! kubectl config get-contexts -o name | grep -q ${CONTEXT}
    then
        echo "Context ${CONTEXT} not found, exiting"
        exit
    fi
    
    # helm
    if ! command -v helm &> /dev/null
    then
        echo "helm not found, downloading...(https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3)"
        echo "Do you want to proceed or you will install helm using your own means?"
        select yn in "Yes" "No"; do
            case $yn in
                Yes ) break;;
                No ) exit;;
            esac
        done
        curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
        chmod 700 get_helm.sh
        ./get_helm.sh
        # Clean up poodle :)
        rm get_helm.sh
        echo "helm installed"
        # TODO verify if HELM repo exist or not yet
        # As helm might exist, but repo - not
        echo "HELM repo add and update..."
        # HELM misc stuff to prepare
        helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >> /dev/null
        helm repo add stable https://charts.helm.sh/stable >> /dev/null
        helm repo add jetstack https://charts.jetstack.io >> /dev/null
        helm repo update >> /dev/null
    fi
    

    # ./yq (as we install mike farah go version)
    # if file yq exist and executable, we assume it is correct version
    if [ ! -f yq ]; then
        echo "yq not found, downloading..."
        VERSION=v4.35.2
        BINARY=yq_linux_amd64
        wget https://github.com/mikefarah/yq/releases/download/${VERSION}/${BINARY} -O yq
        chmod +x yq
        echo "yq installed"
    fi

    # if kernelci-api doesnt exist - clone
    if [ ! -d kernelci-api ]; then
        echo "kernelci-api not found, cloning..."
        git clone https://github.com/kernelci/kernelci-api -b ${BRANCH}

    fi
    # if kernelci-pipeline doesnt exist - clone
    if [ ! -d kernelci-pipeline ]; then
        echo "kernelci-pipeline not found, cloning..."
        git clone https://github.com/kernelci/kernelci-pipeline -b ${BRANCH}
    fi
}

# function for namespace
function recreate_ns {
    # Delete namespace
    echo "Deleting namespace ${NS}... WARNING! this might take up to 5 minutes!"
    kubectl --context=${CONTEXT} delete namespace ${NS_API} || true
    kubectl --context=${CONTEXT} delete namespace ${NS_PIPELINE} || true

    # Create namespace
    echo "Creating namespace ${NS}..."
    kubectl --context=${CONTEXT} create namespace ${NS_API}
    kubectl --context=${CONTEXT} create namespace ${NS_PIPELINE}
    
    # Update namespace in all manifests
    echo "Updating namespace in all manifests in API..."
    for f in kernelci-api/kube/aks/*.yaml; do
        ./yq -i e ".metadata.namespace=\"${NS_API}\"" $f
    done
    echo "Updating namespace in all manifests in Pipeline..."
    for f in kernelci-pipeline/kube/aks/*.yaml; do
        ./yq -i e ".metadata.namespace=\"${NS_PIPELINE}\"" $f
    done

}

function update_fqdn {
    echo "Getting public ip ResourceID for ip ${IP_API} and ${IP_PIPELINE}..."
    PUBLICIPID_API=$(az network public-ip list --query "[?ipAddress!=null]|[?contains(ipAddress, '$IP_API')].[id]" --output tsv)
    if [ "$PUBLICIPID_API" == "" ]; then
        echo "FATAL: IP ResourceID not found for ${IP_API}"
        exit 1
    fi
    PUBLICIPID_PIPELINE=$(az network public-ip list --query "[?ipAddress!=null]|[?contains(ipAddress, '$IP_PIPELINE')].[id]" --output tsv)
    if [ "$PUBLICIPID_PIPELINE" == "" ]; then
        echo "FATAL: IP ResourceID not found for ${IP_PIPELINE}"
        exit 1
    fi

    # Update public IP address with DNS name
    echo "Updating public IP address with DNS names..."
    az network public-ip update --ids $PUBLICIPID_API --dns-name $DNS_API
    az network public-ip update --ids $PUBLICIPID_PIPELINE --dns-name $DNS_PIPELINE

    # Display the FQDN
    az network public-ip show --ids $PUBLICIPID_API --query "[dnsSettings.fqdn]" --output tsv
    az network public-ip show --ids $PUBLICIPID_PIPELINE --query "[dnsSettings.fqdn]" --output tsv

    echo "Updating ingress.yaml with FQDN..."
    HOST_API="${DNS_API}.${LOCATION}.cloudapp.azure.com"
    HOST_PIPELINE="${DNS_PIPELINE}.${LOCATION}.cloudapp.azure.com"
    ./yq -i ".spec.tls[0].hosts[0]=\"${HOST_API}\"" kernelci-api/kube/aks/ingress.yaml
    ./yq -i ".spec.rules[0].host=\"${HOST_API}\"" kernelci-api/kube/aks/ingress.yaml
    ./yq -i ".spec.acme.solvers[0].selector.dnsNames[0]=\"${HOST_API}\"" manifests/issuer.yaml
    ./yq -i ".spec.acme.solvers[1].selector.dnsNames[0]=\"${HOST_PIPELINE}\"" manifests/issuer.yaml
    ./yq -i ".spec.tls[0].hosts[0]=\"${HOST_PIPELINE}\"" kernelci-pipeline/kube/aks/ingress.yaml
    ./yq -i ".spec.rules[0].host=\"${HOST_PIPELINE}\"" kernelci-pipeline/kube/aks/ingress.yaml

    echo "WARNING! You will need to associate IPs in Azure portal to cluster manually"
    echo "Associate public IP address in Public IP addresses section of Azure portal"
}

function deploy_once_pipeline {
    echo "Deploying kernelci-pipeline secrets..."
    echo "Setting secrets...(API token, k8s, toml)"

    kubectl --context=${CONTEXT} delete secret kernelci-api-token --namespace=${NS_PIPELINE} || true
    kubectl --context=${CONTEXT} create secret generic kernelci-api-token --from-literal=token=${API_TOKEN} --namespace=${NS_PIPELINE}

    echo "Setting k8s credentials..."
    kubectl --context=${CONTEXT} delete secret k8scredentials --namespace=${NS_PIPELINE} || true
    kubectl --context=${CONTEXT} create secret generic k8scredentials --from-file=k8s-credentials.tgz=k8s.tgz --namespace=${NS_PIPELINE}

    echo "Setting kernelci-secrets.toml..."
    kubectl --context=${CONTEXT} delete secret pipeline-secrets --namespace=${NS_PIPELINE} || true
    # kernelci-secrets.toml needs to be in the same directory as this script
    kubectl --context=${CONTEXT} create secret generic pipeline-secrets --from-file=kernelci.toml=kernelci-secrets.toml --namespace=${NS_PIPELINE}
    # id_rsa needs to be in the same directory as this script, this one used for NIPA ssh access to upload artifacts
    # do not forget to install public part on storage account for nipa@ user
    echo "Setting id_rsa... this part not tested yet"
    ID_RSA_BASE64=$(cat id_rsa | base64 -w 0)
PATCH_JSON=$(cat <<EOF
{"data": {"id_rsa": "${ID_RSA_BASE64}"}}
EOF
)
    kubectl --context=${CONTEXT} patch secret pipeline-secrets --namespace=${NS_PIPELINE} --patch "${PATCH_JSON}"
    kubectl --context=${CONTEXT} --namespace=${NS_PIPELINE} patch secret pipeline-secrets -p '{"data": {"kcidb-credentials.json": "'$(cat secrets/kcidb-credentials.json | base64 -w 0)'"}}'

    echo "Setting kernelci-api-secret/secret-key"
    kubectl --context=${CONTEXT} delete secret kernelci-api-secret --namespace=${NS_API} || true
    kubectl --context=${CONTEXT} create secret generic kernelci-api-secret --from-literal=secret-key=${API_SECRET_KEY} \
            --from-literal=email-password=${EMAIL_PASSWORD} \
            --namespace=${NS_API}
}


function retrieve_secrets_toml {
    echo "Retrieving kernelci-secrets.toml..."
    kubectl --context=${CONTEXT} get secret pipeline-secrets --namespace=${NS_PIPELINE} -o yaml > allsecrets.yaml
    python3 extract_secret.py allsecrets.yaml kernelci.toml kernelci-secrets.toml
}

function deploy_secrets_toml_only {
    echo "Setting kernelci-secrets.toml..."
    kubectl --context=${CONTEXT} patch secret pipeline-secrets -p '{"data": {"kernelci.toml": "'$(cat kernelci-secrets.toml | base64 -w 0)'"}}' --namespace=${NS_PIPELINE}
}

function deploy_once_api {
    # Set secret
    #echo "Setting secret..."
    #kubectl --context=${CONTEXT} create secret generic kernelci-api-secret --from-literal=secret-key=${SECRET} --namespace=${NS}
    #echo "Secret: ${SECRET}" >> .api-secret.txt

    # replace MONGOCONNECTSTRING in deploy/configmap.yaml.example to ${MONGO} and save to deploy/configmap.yaml
    #cp deploy/configmap.yaml.example deploy/configmap.yaml
    cp kernelci-api/kube/aks/configmap.yaml.example kernelci-api/kube/aks/configmap.yaml
    # if MONGO set
    if [ -z "$MONGO" ]; then
        echo "MONGO not set, using default"
    else
        ./yq -i e ".data.mongo_service=\"${MONGO}\"" kernelci-api/kube/aks/configmap.yaml
    fi
    ./yq -i e ".data.email_sender=\"${EMAIL_USER}\"" kernelci-api/kube/aks/configmap.yaml
    ./yq -i e ".metadata.namespace=\"${NS_API}\"" kernelci-api/kube/aks/configmap.yaml

    # Update all namespaces in deploy/* to ${NS}
    echo "Updating namespaces in deploy/* to ${NS_API}..."
    
    # Deploy configmap
    echo "Deploying configmap..."
    kubectl --context=${CONTEXT} create -f kernelci-api/kube/aks/configmap.yaml --namespace=${NS_API}
}

function deploy_nginx {
    echo "Deploying ingress-nginx for api... ${NS_API}"
    helm install ingress-nginx ingress-nginx/ingress-nginx \
        -n ${NS_API} \
        --kube-context=${CONTEXT} \
        --set controller.replicaCount=1 \
        --set controller.nodeSelector."beta\.kubernetes\.io/os"=linux \
        --set defaultBackend.nodeSelector."beta\.kubernetes\.io/os"=linux \
        --set controller.service.externalTrafficPolicy=Local \
        --set controller.service.loadBalancerIP="${IP_API}" \
        --set controller.service.annotations."service\.beta\.kubernetes\.io/azure-dns-label-name"="${DNS_API}" \
        --set controller.publishService.enabled=true \
        --set controller.service.annotations."service\.beta\.kubernetes\.io/azure-load-balancer-resource-group"="${AZURE_RG}" \
        --set controller.ingressClassResource.name=ingressclass-api \
        --set controller.ingressClassResource.controllerValue="k8s.io/ingress-nginx-api" \
        --set controller.ingressClassResource.enabled=true \
        --set controller.IngressClassByName=true \
        --set controller.scope.enabled=true \
        --set controller.scope.namespace=${NS_API} \

    echo "Deploying ingress-nginx for pipeline..."
    helm install ingress-nginx2 ingress-nginx/ingress-nginx \
        -n ${NS_PIPELINE} \
        --kube-context=${CONTEXT} \
        --set controller.replicaCount=1 \
        --set controller.nodeSelector."beta\.kubernetes\.io/os"=linux \
        --set defaultBackend.nodeSelector."beta\.kubernetes\.io/os"=linux \
        --set controller.service.externalTrafficPolicy=Local \
        --set controller.service.loadBalancerIP="${IP_PIPELINE}" \
        --set controller.service.annotations."service\.beta\.kubernetes\.io/azure-dns-label-name"="${DNS_PIPELINE}" \
        --set controller.publishService.enabled=true \
        --set controller.service.annotations."service\.beta\.kubernetes\.io/azure-load-balancer-resource-group"="${AZURE_RG}" \
        --set controller.ingressClassResource.name=ingressclass-pipeline \
        --set controller.ingressClassResource.controllerValue="k8s.io/ingress-nginx-pipeline" \
        --set controller.ingressClassResource.enabled=true \
        --set controller.IngressClassByName=true \
        --set controller.scope.enabled=true \
        --set controller.scope.namespace=${NS_PIPELINE}
}

function patch_nginx_config {
    echo "Patching nginx config..."
    # patch nginx config for large lava callbacks
    # set:
    # data:
    #   client-max-body-size: 128m
    kubectl --context=${CONTEXT} patch configmap ingress-nginx2-controller -n ${NS_PIPELINE} --type merge --patch '{"data":{"proxy-body-size":"128m"}}'
    # update creationTimestamp
    kubectl --context=${CONTEXT} patch configmap ingress-nginx2-controller -n ${NS_PIPELINE} --type merge --patch '{"metadata":{"creationTimestamp":null}}'
    # redeploy ingress-nginx
    kubectl --context=${CONTEXT} rollout restart deployment ingress-nginx2-controller -n ${NS_PIPELINE}
}

function deploy_cert_manager {
    #helm uninstall --kube-context=${CONTEXT} -n kernelci-api cert-manager
    # Deploy cert-manager for default namespace
    echo "Deploying cert-manager..."
    # check if cert-manager already exist by helm
    if helm --kube-context=${CONTEXT} list | grep -q cert-manager; then
        echo "cert-manager already exist, skipping"
    else
        echo "Installing cert-manager..."
        helm install --kube-context=${CONTEXT} cert-manager jetstack/cert-manager --set installCRDs=true
    fi
}

function redeploy_certissuer {
    echo "Updating cert-issuer ACME url to ${ACME_URL}..."
    ./yq -i e ".spec.acme.server=\"${ACME_URL}\"" manifests/issuer.yaml
    kubectl --context=${CONTEXT} apply -f manifests/issuer.yaml
}

function deploy_certissuer {
    echo "Deploying cert-issuer..."
    kubectl --context=${CONTEXT} apply -f manifests/issuer.yaml
}

function deploy_pipeline_configmap {
    CFGDIR="config/*"
    # if $2 is set, then we use it as a cfg directory
    if [ ! -z "$1" ]; then
        echo "Using ${1} as config directory"
        CFGDIR="$1"
    else
        echo "Using default config directory"
    fi
    # replace in config/ all 'https://staging.kernelci.org:9100' to 'https://kernelci-pipeline.westus3.cloudapp.azure.com'
    echo "Updating pipeline-configmap..."
    sed -i 's|https://staging.kernelci.org:9100|https://${DNS_PIPELINE}.${LOCATION}.cloudapp.azure.com|g' ${CFGDIR}/*.yaml

    echo "Deleting old pipeline-configmap..."
    kubectl --context=${CONTEXT} delete configmap pipeline-configmap --namespace=${NS_PIPELINE} || true
    echo "Deploying pipeline-configmap..."
    kubectl --context=${CONTEXT} create configmap pipeline-configmap --namespace=${NS_PIPELINE} --from-file=${CFGDIR}/
}

function deploy_pipeline {
    echo "Deploying kernelci-pipeline services..."

    deploy_pipeline_configmap

    # Deploy nodehandlers (timeout,etc)
    echo "Deploying nodehandlers..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/nodehandlers.yaml --namespace=${NS_PIPELINE}

    # Deploy scheduler-k8s
    echo "Deploying scheduler-k8s..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/scheduler-k8s.yaml --namespace=${NS_PIPELINE}

    echo "Deploying scheduler-shell..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/scheduler-shell.yaml --namespace=${NS_PIPELINE}

    # Deploy scheduler-lava
    echo "Deploying scheduler-lava..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/scheduler-lava.yaml --namespace=${NS_PIPELINE}

    # Deploy tarball
    echo "Deploying tarball..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/tarball.yaml --namespace=${NS_PIPELINE}

    # Deploy trigger
    echo "Deploying trigger..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/trigger.yaml --namespace=${NS_PIPELINE}

    # Deploy trigger
    echo "Deploying trigger..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/lava-callback.yaml --namespace=${NS_PIPELINE}

    # Deploy ingress
    echo "Deploying ingress..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/ingress.yaml --namespace=${NS_PIPELINE}

    # Deploy pipeline-kcidb
    echo "Deploying pipeline-kcidb..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/pipeline-kcidb.yaml --namespace=${NS_PIPELINE}

    # Deploy pipeline-regression
    echo "Deploying pipeline-regression..."
    kubectl --context=${CONTEXT} apply -f kernelci-pipeline/kube/aks/regression.yaml --namespace=${NS_PIPELINE}

    sleep 5

    # wait until ingress is ready
    #echo "Waiting for pipeline ingress to be ready..."
    #kubectl --context=${CONTEXT} wait --for=condition=ready --timeout=300s ingress pipeline-ingress --namespace=${NS_PIPELINE}
}

function deploy_api {
    # Deploy redis
    echo "Deploying redis..."
    kubectl --context=${CONTEXT} apply -f kernelci-api/kube/aks/redis.yaml --namespace=${NS_API}

    # Deploy API
    echo "Deploying API Deployment..."
    kubectl --context=${CONTEXT} apply -f kernelci-api/kube/aks/api.yaml --namespace=${NS_API}

    # Deploy ingress
    echo "Deploying ingress..."
    kubectl --context=${CONTEXT} apply -f kernelci-api/kube/aks/ingress.yaml --namespace=${NS_API}
    sleep 5

    # wait until ingress is ready
    #echo "Waiting for api ingress to be ready..."
    #kubectl --context=${CONTEXT} wait --for=condition=ready --timeout=300s ingress api-ingress --namespace=${NS_API}
}

function backup_pipeline_configmap {
    echo "Backup pipeline-configmap..."
    kubectl --context=${CONTEXT} get configmap pipeline-configmap --namespace=${NS_PIPELINE} -o yaml > pipeline-configmap.yaml
}



if [ -z "$AZURE_RG" ]; then
    echo "AZURE_RG not set, not an Azure deployment"
    echo "You need to retrieve the static IP address of the ingress controller and set the IP variable in pipeline-initial-deploy.cfg"
    exit 1
fi




# Local toolset setup
local_setup

if [ "$1" == "config" ]; then
    deploy_pipeline_configmap $2
    exit 0
fi

if [ -z "$API_TOKEN" ]; then
    # TODO(nuclearcat): reference to documentation
    echo "API_TOKEN not set, please follow procedure to create users and issue token after deployment"
fi

if [ -z "$API_SECRET_KEY" ]; then
    # TODO(nuclearcat): reference to documentation
    echo "API_SECRET_KEY not set. Suggested to keep it persistent for same token"
fi

if [ -z "$MONGO" ]; then
    echo "MONGO not set, exiting"
    exit 1
fi

if [ -z "$EMAIL_USER" ] || [ -z "$EMAIL_PASSWORD" ]; then
    echo "EMAIL_USER or EMAIL_PASSWORD not set, exiting"
    exit 1
fi


# if argument delete set just delete namespace and exit
if [ "$1" == "delete" ]; then
    echo "Deleting namespace ${NS_API} and ${NS_PIPELINE}..."
    kubectl --context=${CONTEXT} delete namespace ${NS_API} || true
    kubectl --context=${CONTEXT} delete namespace ${NS_PIPELINE} || true
    exit 0
fi

if [ "$1" == "token" ]; then
    # verify k8s.tgz, this file should(might) contain:
    # .kube , .config/gcloud , .azure
    if [ ! -f k8s.tgz ]; then
        echo "k8s.tgz not found, exiting"
        exit 1
    fi

    #echo "Post-install procedure, installing API token"
    #echo "Deleting old secrets..."
    #kubectl --context=${CONTEXT} delete secret kernelci-api-secret --namespace=${NS_API} || true
    #kubectl --context=${CONTEXT} delete secret kernelci-api-token --namespace=${NS_PIPELINE} || true
    #echo "Setting kernelci-api-secret/secret-key for API and Pipeline"
    #kubectl --context=${CONTEXT} create secret generic kernelci-api-secret --from-literal=secret-key=${API_TOKEN} --namespace=${NS_API}
    #kubectl --context=${CONTEXT} create secret generic kernelci-api-token --from-literal=token=${API_TOKEN} --namespace=${NS_PIPELINE}
    deploy_pipeline_configmap
    deploy_once_pipeline
    exit 0
fi

# if argument backup-config set just backup pipeline configmap and exit
if [ "$1" == "backup-config" ]; then
    backup_pipeline_configmap
    exit 0
fi

# cert
if [ "$1" == "cert" ]; then
    redeploy_certissuer
    exit 0
fi

# pipeline-credentials
if [ "$1" == "pipeline-credentials" ]; then
    # verify k8s.tgz, this file should(might) contain:
    # .kube , .config/gcloud , .azure
    if [ ! -f k8s.tgz ]; then
        echo "k8s.tgz not found, exiting"
        exit 1
    fi
    deploy_once_pipeline
    exit 0
fi

# pipeline-configmap
if [ "$1" == "pipeline-configmap" ]; then
    deploy_pipeline_configmap
    exit 0
fi

# pipeline-restart-pods
if [ "$1" == "pipeline-restart-pods" ]; then
    kubectl --context=${CONTEXT} delete pods --all --namespace=${NS_PIPELINE}
    exit 0
fi

if [ "$1" == "api-restart-pods" ]; then
    kubectl --context=${CONTEXT} delete pods --all --namespace=${NS_API}
    exit 0
fi

# backup-mongo
if [ "$1" == "backup-mongo" ]; then
    echo "Backup MongoDB at ${MONGO}..."
    echo "Cleaning up old backup directory on k8s..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "rm -rf /tmp/mongobackup"
    echo "Cleaning old tarball..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "rm -rf /tmp/mongobackup.tar.gz"
    echo "Backing up mongo..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "mongodump --uri=${MONGO} --out=/tmp/mongobackup --gzip"
    echo "Print sizes..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "du -sh /tmp/mongobackup"
    echo "Create tarball..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "tar -czf /tmp/mongobackup.tar.gz /tmp/mongobackup"
    echo "Copying backup..."
    kubectl --context=${CONTEXT} cp --retries 10 ${NS_API}/$(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}'):/tmp/mongobackup.tar.gz ./mongobackup.tar.gz
    echo "Renaming backup..."
    mv mongobackup.tar.gz mongobackup-$(date +%Y%m%d%H%M%S).tar.gz
    echo "Backup done"
    exit 0
fi

# restore-mongo
if [ "$1" == "restore-mongo" ]; then
    echo "Restoring MongoDB at ${MONGO}..."
    echo "Cleaning up old backup directory on k8s..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "rm -rf /tmp/mongobackup"
    echo "Copying backup..."
    kubectl --context=${CONTEXT} cp ./mongobackup ${NS_API}/$(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}'):/tmp/mongobackup
    echo "Restoring mongo..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "mongorestore --drop --uri=${MONGO} --gzip /tmp/mongobackup"
    exit 0
fi

# mongo-shell
if [ "$1" == "mongo-shell" ]; then
    echo "Mongo shell..."
    kubectl --context=${CONTEXT} exec -n ${NS_API} -it $(kubectl --context=${CONTEXT} get pods -n ${NS_API} -l app=mongo -o jsonpath='{.items[0].metadata.name}') -- /bin/sh -c "mongosh kernelci"
    exit 0
fi

# patch nginx config
if [ "$1" == "patch-nginx" ]; then
    patch_nginx_config
    exit 0
fi

# deploy_secret_toml_only
if [ "$1" == "deploy_secret_toml_only" ]; then
    deploy_secrets_toml_only
    exit 0
fi

# retrieve_secrets_toml
if [ "$1" == "retrieve_secrets_toml" ]; then
    retrieve_secrets_toml
    exit 0
fi

# retrieve_k8s_credentials
if [ "$1" == "retrieve_k8s_credentials" ]; then
    echo "Retrieving k8s credentials..."
    kubectl --context=${CONTEXT} get secret k8scredentials --namespace=${NS_PIPELINE} -o yaml > k8s-credentials.yaml
    python3 extract_secret.py k8s-credentials.yaml k8s-credentials.tgz k8s-old.tgz
    exit 0
fi

# update_k8s_credentials
if [ "$1" == "update_k8s_credentials" ]; then
    # check if k8s-new.tgz exist
    if [ ! -f k8s-new.tgz ]; then
        echo "k8s-new.tgz not found, exiting"
        exit 1
    fi
    echo "Updating k8s credentials..."
    kubectl --context=${CONTEXT} delete secret k8scredentials --namespace=${NS_PIPELINE} || true
    kubectl --context=${CONTEXT} create secret generic k8scredentials --from-file=k8s-credentials.tgz=k8s-new.tgz --namespace=${NS_PIPELINE}
    exit 0
fi

# require full option or quit
if [ "$1" != "full" ]; then
    echo "Usage:"
    echo "$0 full - deploy everything"
    echo "$0 delete - delete all namespaces"
    echo "$0 token - post-install procedure, install API token"
    echo "$0 backup-config - backup pipeline configmap"
    echo "$0 config - deploy pipeline configmap"
    echo "$0 pipeline-credentials - deploy pipeline credentials"
    echo "$0 pipeline-configmap - deploy pipeline configmap"
    echo "$0 pipeline-restart-pods - restart all pods in pipeline"
    echo "$0 api-restart-pods - restart all pods in api"
    echo "$0 backup-mongo - backup mongo"
    echo "$0 restore-mongo - restore mongo"
    echo "$0 mongo-shell - mongo shell"
    echo "$0 patch-nginx - patch nginx config"
    echo "$0 deploy_secret_toml_only - deploy kernelci-secrets.toml only"
    echo "$0 retrieve_secrets_toml - retrieve kernelci-secrets.toml"
    echo "$0 retrieve_k8s_credentials - retrieve k8s credentials"
    echo "$0 update_k8s_credentials - update k8s credentials"
    exit 1
fi

# verify kernelci-secrets.toml
if [ ! -f kernelci-secrets.toml ]; then
    echo "kernelci-secrets.toml not found, exiting"
    echo "Please review example file in same directory and create one"
    exit 1
fi

# if IP_API or IP_PIPELINE not set, initial set, allocate static ip and update fqdn
if [ -z "$IP_API" ] || [ -z "$IP_PIPELINE" ]; then
    echo "Likely you are running script first time"
    echo "It will assign static IP and update DNS"
    echo "Then give your cluster permission to use public IPs"
    fetch_static_ips
    update_fqdn
    azure_ip_permissions
    echo "Waiting IP to propagate, 30 seconds..."
    sleep 30
fi

# verify k8s.tgz, this file should(might) contain:
# .kube , .config/gcloud , .azure
if [ ! -f k8s.tgz ]; then
    echo "k8s.tgz not found, exiting"
    exit 1
fi

azure_ip_permissions
update_fqdn
recreate_ns
deploy_cert_manager
deploy_nginx
deploy_once_api
deploy_once_pipeline
deploy_api
deploy_pipeline
redeploy_certissuer

echo "----------------------------------------"
echo "Done"
echo "Test API availability by curl https://${DNS_API}.${LOCATION}.cloudapp.azure.com/latest/"
echo "Test lava callback availability by curl https://${DNS_PIPELINE}.${LOCATION}.cloudapp.azure.com/"

