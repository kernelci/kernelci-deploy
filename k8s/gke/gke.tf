# FIXME: For real deployment we should store the terraform state
# in cloud storage rather than just the current directory, terraform
# supports Azure blob storage directly. This means configuration
# doesn't need to be on a single machine somewhere.
#
# See https://developer.hashicorp.com/terraform/language/settings/backends/gcs
#
#terraform {
#  backend "gcs" {
#    resource_group_name  = "kernelci-tf-storage"
#    storage_account_name = "kernelci-tf"
#    container_name       = "tfstate"
#    key                  = "workers.terraform.tfstate"
#  }
#}

#variable "gke_username" {
#  default     = ""
#  description = "gke username"
#}

#variable "gke_password" {
#  default     = ""
#  description = "gke password"
#}

locals {
  regions = toset([
    "us-central1",
    "europe-west2",
  ])
}

# GKE cluster
resource "google_container_cluster" "primary" {
  for_each = local.regions

  name     = "${each.key}-workers"
  location = each.key
  
  # We can't create a cluster with no node pool defined, but we want to only use
  # separately managed node pools. So we create the smallest possible default
  # node pool and immediately delete it.
  remove_default_node_pool = true
  initial_node_count       = 1

  network    = "${each.key}-vpc"
  subnetwork = "${each.key}-subnet"
}

# Smaller nodes for most jobs
resource "google_container_node_pool" "small_nodes" {
  for_each = local.regions

  name       = "${each.key}-small-node-pool"
  location   = each.key
  cluster    = "${each.key}-workers"

  node_config {
    oauth_scopes = [
      "https://www.googleapis.com/auth/logging.write",
      "https://www.googleapis.com/auth/monitoring",
    ]

    labels = {
      "kernelci/worker" = "worker"
      "kernelci/worker-size" = "small"
    }

    # Standard machine, 8 vCPUs, 30G memory
    machine_type = "n1-standard-8"
    preemptible  = true
    spot = true
    tags         = [
       "kernelci/worker",
       "kernelci/small-worker"
    ]
    
    metadata = {
      disable-legacy-endpoints = "true"
    }
  }

  autoscaling {
    min_node_count = 1
    max_node_count = 10
  }
}

# Bigger nodes for all*config jobs
resource "google_container_node_pool" "big_nodes" {
  for_each = local.regions

  name       = "${each.key}-big-node-pool"
  location   = each.key
  cluster    = "${each.key}-workers"

  node_config {
    oauth_scopes = [
      "https://www.googleapis.com/auth/logging.write",
      "https://www.googleapis.com/auth/monitoring",
    ]

    labels = {
      "kernelci/worker" = "worker"
      "kernelci/worker-size" = "big"
    }

    # Standard machine, 32 vCPUs, 128G (?) memory
    machine_type = "n2-standard-32"
    preemptible  = true
    spot = true
    tags         = [
       "kernelci/worker",
       "kernelci/big-worker"
    ]
    
    metadata = {
      disable-legacy-endpoints = "true"
    }
  }

  autoscaling {
    min_node_count = 1
    max_node_count = 10
  }
}
