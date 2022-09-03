# Store the terraform state in cloud storage rather than just the
# current directory, terraform supports Azure blob storage directly.
# This means configuration doesn't need to be on a single machine
# somewhere.
#
# See https://www.terraform.io/language/settings/backends/azurerm
#
terraform {
  backend "azurerm" {
    resource_group_name  = "kernelci-tf-storage"
    storage_account_name = "kernelci-tf"
    container_name       = "tfstate"
    key                  = "workers.terraform.tfstate"
  }
}

provider "azurerm" {
  features {}
}

# We assign all clusters to the same resource group, this is purely for
# accounting purposes so it doesn't matter where the resource group is
resource "azurerm_resource_group" "workers" {
  name     = "kernelci-workers"
  location = "East US"

  tags = {
    environment = "kernelci-workers"
  }
}

locals {
  zones = toset([
    "francecentral",
    "uksouth",
    "eastus2",
  ])
}

resource "azurerm_kubernetes_cluster" "workers" {
  for_each = local.zones

  name                = "${each.key}-workers-aks"
  location            = each.key
  resource_group_name = azurerm_resource_group.workers.name
  dns_prefix          = "${each.key}-workers-k8s"

  # Automatically roll out upgrades from AKS
  automatic_channel_upgrade = "stable"

  # Single always present node as AKS requires a default node pool -
  # Terraform and/or AKS don't let us tag this as a spot instance and
  # ideally we can scale the builders down to 0 so this is a small
  # instance not tagged for work.
  default_node_pool {
    name            = "default"
    node_count      = 1
    vm_size         = "Standard_DS2_v2"
    os_disk_size_gb = 30

    node_labels = {
      "kernelci/management" = "management"
    }
  }

  service_principal {
    client_id     = var.appId
    client_secret = var.password
  }

  role_based_access_control {
    enabled = true
  }

  tags = {
    environment = "kernelci"
  }
}

# Smaller nodes for most jobs
resource "azurerm_kubernetes_cluster_node_pool" "small_workers" {
  for_each = azurerm_kubernetes_cluster.workers

  name = "smallworkers"
  kubernetes_cluster_id = each.value.id

  # 3rd gen Xeon 8 cores, 32G RAM - general purpose
  vm_size               = "Standard_D8s_v5"

  # Currently things struggle with scale to 0 so require a node
  enable_auto_scaling = true
  min_count  = 1
  node_count = 1
  max_count  = 10

  priority = "Spot"
  # We could set this lower to control costs, -1 means up to on demand
  # price
  spot_max_price = -1

  node_labels = {
    "kernelci/worker" = "worker"
    "kernelci/worker-size" = "small"
  }
}

# Big nodes for more intensive jobs (and large numbers of small jobs)
resource "azurerm_kubernetes_cluster_node_pool" "big_workers" {
  for_each = azurerm_kubernetes_cluster.workers

  name = "bigworkers"
  kubernetes_cluster_id = each.value.id

  # 3rd gen Xeon, 32 core, 64G RAM - compute optimised
  vm_size               = "Standard_F32s_v2"

  # Currently things struggle with scale to 0 so require a node
  enable_auto_scaling = true
  min_count  = 1
  node_count = 1
  max_count  = 10

  priority = "Spot"
  # We could set this lower to control costs, -1 means up to on demand
  # price
  spot_max_price = -1

  node_labels = {
    "kernelci/worker" = "worker"
    "kernelci/worker-size" = "big"
  }
}
