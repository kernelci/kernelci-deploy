#!/bin/bash
# (c) Collabora, 2023
# Script to enable K8S MSI on runner VMs
# (This allows to generate kubeconfig without using az login and storing credentials on runner VMs)

# arguments: vmname vmrg role aksrg
# vmname: VM name
# vmrg: VM resource group
# role: role to assign to VM for AKS resource group
# aksrg: AKS resource group
function vm_msi_k8s() {
    VM_NAME=$1
    VM_RG=$2
    AKS_ROLE=$3
    AKS_RG=$4


    echo "Enable MSI on VM ${VM_NAME} in resource group ${VM_RG}"
    # Enable MSI on VM
    az vm identity assign -g ${VM_RG} -n ${VM_NAME}
    if [ $? -ne 0 ]; then
        echo "Failed to assign identity to VM ${VM_NAME} in resource group ${VM_RG}"
        exit 1
    fi

    # Get VM MSI principal ID and resource group ID for all clusters (AKS)
    # TODO: Hardcoded for now, maybe can retrieve later
    VM_PRINCIPAL_ID=$(az vm show -n ${VM_NAME} -g ${VM_RG} --query identity.principalId -o tsv)
    RGAKS_ID=$(az group show -n ${AKS_RG} --query id -o tsv)
    
    echo "VM_PRINCIPAL_ID: $VM_PRINCIPAL_ID"
    echo "RGAKS_ID: $RGAKS_ID"
    if [ -z "$VM_PRINCIPAL_ID" ] || [ -z "$RGAKS_ID" ]; then
        echo "Failed to get VM_PRINCIPAL_ID or RGAKS_ID"
        exit 1
    fi

    # Assign role to VM MSI
    echo "Ignore warning Failed to query xxxxx by invoking Graph API"
    az role assignment create --role $AKS_ROLE --assignee $VM_PRINCIPAL_ID --scope $RGAKS_ID
    if [ $? -ne 0 ]; then
        echo "Failed to assign role to VM MSI ${VM_PRINCIPAL_ID} in resource group ${RGAKS_ID}"
        exit 1
    fi

    # Instructions for managed VM (runner)
    echo "# Here is tricky part, this is doesn't apply immediately, so we need to wait for a while (5min?)"
    echo "# After that on managed VM $VM_NAME we should run:"
    echo az login --identity
    echo az aks get-credentials --resource-group $AKS_RG --name AKS_NAME

    echo "------------------------------------------------------------------------------------------------------------------"
    echo "Completed VM MSI $VM_NAME"
}

vm_msi_k8s vm-staging-001 RG-KERNELCI-WESTUS3-001 Contributor kernelci-api-staging

