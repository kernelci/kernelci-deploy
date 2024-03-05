#!/bin/bash
# This is script to automate creation of computing/build clusters for KernelCI
# Changelog:
# xxxx: Changed "control" node from Standard_D4s_v4 to Standard_D2s_v5
# 2024-03-05: Initial git submit/release

# Arguments: rgname name location vmsize maxcount
function create_cluster () {
    echo "Creating cluster $2 in resource group $1 at location $3 with VM size $4 max-count $5 and spot-max-price $6"

    echo "Create group $1 at location $3"
    az group create --name $1 --location $3

    echo "Delete cluster $2 if exist"
    az aks delete --resource-group $1 --name $2 --yes

    # this is default nodepool, so max-count is 1
    echo "Create cluster $2"
    # T
    # was --node-vm-size Standard_D4s_v4
    az aks create -g $1 -n $2 --enable-managed-identity --node-count 1 --generate-ssh-keys --node-vm-size Standard_D2as_v5 \
        --load-balancer-sku standard \
        --enable-cluster-autoscaler \
        --min-count 1 \
        --max-count 1
    if [ $? -ne 0 ]; then
        echo "Failed to create cluster $2 in resource group $1 at location $3 with control node VM size Standard_D2s_v5 and max-count 1"
        exit 1
    fi
    
    echo "Add spotnodepool to cluster $2 with spot-max-price $6"
    az aks nodepool add --resource-group $1 --cluster-name $2 --name spotnodepool --priority Spot --eviction-policy Delete \
        --spot-max-price $6 --enable-cluster-autoscaler --min-count 0 --max-count $5 --no-wait --node-vm-size $4
    if [ $? -ne 0 ]; then
        echo "Failed to add spotnodepool to cluster $2 in resource group $1 at location $3 with VM size $4 and max-count $5 and spot-max-price $SPOT_MAX_PRICE"
        exit 1
    fi

    # Get credentials for kubectl, propagate it to pipeline configuration!
    az aks get-credentials --resource-group $1 --name $2
    echo "Cluster $2 created"
}

function nodepool() {
    echo "Add spotnodepool to cluster $2 with spot-max-price $6"
    az aks nodepool add --resource-group $1 --cluster-name $2 --name spotnodepool --priority Spot --eviction-policy Delete \
        --spot-max-price $6 --enable-cluster-autoscaler --min-count 0 --max-count $5 --no-wait --node-vm-size $4
    if [ $? -ne 0 ]; then
        echo "Failed to add spotnodepool to cluster $2 in resource group $1 at location $3 with VM size $4 and max-count $5 and spot-max-price $SPOT_MAX_PRICE"
        exit 1
    fi

    az aks get-credentials --resource-group $1 --name $2

}

function install_secrets() {
    echo "Install secrets for cluster $1"
    API_TOKEN_STAGING=$(cat secrets/token)
    AZURE_SAS_STAGING=$(cat secrets/azure_sas_staging)
    kubectl --context=$1 delete secret kci-api-jwt-staging
    kubectl --context=$1 delete secret kci-api-azure-files-sas-staging
    kubectl --context=$1 create secret generic kci-api-jwt-staging --from-literal=token="${API_TOKEN_STAGING}"
    kubectl --context=$1 create secret generic kci-api-azure-files-sas-staging --from-literal=azure-files-sas="${AZURE_SAS_STAGING}"
}

### After creation we might have nodepool with some unnecessary running nodes, it needs about 10-15 min to scale down

# Big cluster (NOT TESTED YET)
#create_cluster rg-kbuild-westus3 aks-kbuild-big-1 westus3 Standard_F32s_v2 4 0.1375
#install_secrets aks-kbuild-big-1

#create_cluster rg-kbuild-westus3 aks-kbuild-medium-1 westus3 Standard_D8as_v5 13 0.04
#install_secrets aks-kbuild-medium-1
