# KernelCI API/Pipeline Kubernetes deployment

This document describes how to deploy the KernelCI API and Pipeline services
using Kubernetes.
We cover initial deployment, and regular updates of the services.

## Prerequisites

### Configuration files

The deployment uses a set of configuration files to define the services and
their configuration.
Some of files contain sensitive information, such as passwords and API keys,
they are available in separate repositories and are not included in this
repository.

Such configuration files are:

- `deploy.cfg`: Main configuration file for the deployment.
Contains following variables, set as "export VARIABLE=PARAMETER"
API_TOKEN - API Token used by Pipeline to authenticate with API
MONGO - MongoDB connection string
API_SECRET_KEY - Secret key used by API to sign JWT tokens
EMAIL_USER - Email address used by API/Pipeline to send emails
EMAIL_PASSWORD - Password for the email address
IP_API - IP address of the API service namespace endpoint, might be detected automatically
IP_PIPELINE - IP address of the Pipeline service namespace endpoint, might be detected automatically

- k8s.tgz - Kubernetes configuration files to access build clusters (k8s-credentials)

Other configuration options available:

api-pipeline-deploy.sh contains several variables that can be set to customize the deployment:

```
# Azure resource group and location for cluster deployment
AZURE_RG="kernelci-api-1_group"
LOCATION="westus3"

# Cluster name and context (kubectl)
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
# If you are deploying a production instance, use production
ACME="staging"

# git branch for kernelci-api and pipeline projects, normally it is
# stable snapshot: kernelci.org or main
BRANCH="main"
```

## Deployment

### Initial deployment

To deploy the KernelCI API and Pipeline services, run the following command:

```
./api-pipeline-deploy.sh
```

This script will deploy the services to a Kubernetes cluster, create the
necessary namespaces, and configure the services.

Details of the deployment can be found in the `api-pipeline-deploy.sh` script.
We will document the deployment process in more detail in the future.

### Updating the deployment

To update the deployment, run the following command:

```
./api-production-update.sh
```

This script will update the deployment to the latest version of the services.
This script does the following:

- Pulls the latest versions of kernelci-core, kernelci-api, kernelci-pipeline
repositories, build latest docker images, record their sha256sums and push them to the registry.
- Update k8s manifests with the new sha256sums
- Apply the new manifests to the cluster, so the new images are deployed
- Upload new configuration files to the cluster (kernelci-pipeline/config as pipeline configmap)
- Restart the pipeline service to apply the new configuration

After production it is recommended to execute:
`kubectl get pods -n kernelci-pipeline` and `kubectl get pods -n kernelci-api` to check if the pods are running correctly,
not restarting and not in error state.

After that you can check the services are running correctly by accessing the API and Pipeline services endpoints.

