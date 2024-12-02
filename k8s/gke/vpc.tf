variable "project_id" {
  description = "project id"
}

variable "region" {
  description = "region"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# VPC
resource "google_compute_network" "vpc" {
  for_each = local.regions
  name                    = "${each.key}-vpc"
  auto_create_subnetworks = "false"
}

# Subnet
resource "google_compute_subnetwork" "subnet" {
  for_each	= local.regions
  name          = "${each.key}-subnet"
  region        = each.key
  network       = google_compute_network.vpc[each.value].name
  ip_cidr_range = "10.10.0.0/24"
}
