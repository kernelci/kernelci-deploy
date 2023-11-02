<!--
SPDX-License-Identifier: LGPL-2.1-or-later

Copyright (C) 2023 Collabora Limited
Author: Guillaume Tucker <guillaume.tucker@collabora.com>
Author: Denys Fedoryshchenko <denys.f@collabora.com>
-->

KernelCI API/Pipeline Deployment in Azure Kubernetes Services (AKS)
==========================================================

This guide goes through all the steps required to deploy the KernelCI API
service in
[AKS](https://azure.microsoft.com/en-us/products/kubernetes-service).  It
relies on an external Atlas account for MongoDB and does not include any
storage solution.

## Azure account

The first prerequisite is to have a [Microsoft Azure](https://azure.com)
account.  If you already have one, you can skip this step and carry on with
setting up an AKS cluster.  Otherwise, please create one now typically with an
`@outlook.com` email address.  You can create a new address on
[outlook.com](https://outlook.com) for this purpose if needed.

## AKS cluster

Please create an AKS cluster via the [Azure
portal](https://portal.azure.com/#create/Microsoft.AKS).  The standard settings
are fine for this use-case although you might need to adjust a few things to
match the scale of your particular deployment.

## Atlas MongoDB account

This AKS reference deployment relies on an Atlas account for MongoDB in order
to keep everything in the Cloud and application setup to the bare minimal.  As
such, please [create an Atlas
account](https://www.mongodb.com/cloud/atlas/register) if you don't already
have one and set up a database.  The MongoDB service string will be needed
later on to let the API service connect to it.

The recommended way to set up a subscription is to create a "MongoDB Atlas
(pay-as-you-go)" resource via the Azure Marketplace.

To set up a database:

* create a cluster with the appropriate tier for the deployment
* add a database in the project via the web UI with "Create Database"
* go to "Database Access" to create a user and password with the
  `readWriteAnyDatabase` built-in role

You should now be able to connect to the database with a connection string of
the form `mongodb+srv://user:password@something.mongodb.net`.  To verify it's
working:

```
$ mongo mongodb+srv://user:password@something.mongodb.net
[...]
MongoDB Enterprise atlas-ucvcf2-shard-0:PRIMARY> show databases
admin      0.000GB
kernelci   0.004GB
local     22.083GB
```

The string used with the `mongo` shell here is the same one that needs to be
stored as a Kubernetes secret for the API service as described in a later
section of this documentation page.

## Command line tools

Configuring this AKS deployment relies on `az`, `kubectl` and `helm` to be
installed. Deploy script will verify if all of them are installed.

## Get `kubectl` credentials

The container started in the previous section has a `home` directory mounted
from the host.  This allows Azure credentials to be stored there persistently,
so if the container is restarted they'll still be available.

To create the initial credentials, you can run the `az login` command from
within the container and then login by pasting the temporary code into the [web
page](https://microsoft.com/devicelogin):

```
$ az login --use-device-code
To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code XXXYYYZZZ to authenticate.
```

After following the instructions on the web page, you should be able to access
your AKS cluster.  Still from within the container, run the following commands
with your own cluster name instead of `kernelci-api-1`:

```
$ az aks list -o table
Name                    Location        ResourceGroup              KubernetesVersion    [...]
----------------------  --------------  -------------------------  -------------------  [...]
kernelci-api-1          eastus          kernelci-api               1.26.6               [...]
$ az aks get-credentials -n kernelci-api-1 -g kernelci-api
Merged "kernelci-api-1" as current context in /tmp/home/.kube/config
$ kubectl config use-context kernelci-api-1
Switched to context "kernelci-api-1".
$ kubectl get nodes
NAME                                STATUS   ROLES   AGE    VERSION
aks-agentpool-23485665-vmss000000   Ready    agent   4h3m   v1.26.6
aks-userpool-23485665-vmss000000    Ready    agent   4h3m   v1.26.6
```

### Add variables to the deployment script

Deployment script `api-pipeline-deploy.sh` needs few variables to be set.
Please edit the script and set the following variables:

* AZURE_RG="kernelci-api-staging"
Azure resource group name. This is the name of the resource group where AKS cluster is deployed.

* LOCATION="eastus"
Location of the AKS cluster.

* CONTEXT="kernelci-api-staging-1-admin"
kubectl context name. This is the name of the context created by `az aks get-credentials` command.

* CLUSTER_NAME="kernelci-api-staging-1"
AKS cluster name.

* NS_API="kernelci-api-testns"
Kubernetes namespace name. This is the name of the namespace where API will be deployed.

* DNSLABEL_API="kernelci-api-staging"
DNS label for the API service. Full hostname will look like ${DNSLABEL}.${LOCATION}.cloudapp.azure.com

* MONGO=""
MongoDB connection string. This is the connection string to the MongoDB database created in the previous section.

### Deploy the API+Pipeline services

Now that the `kubectl` credentials are set up, you can run the deployment by running the following command:

```
$ ./api-pipeline-deploy.sh
```

Then if all went well, you will see following output:

```
Test API availability by curl https://HOSTNAME/latest/
Test LAVA callback by curl https://HOSTNAME/
```

Example:
```
$ curl https://kernelci-api.eastus.cloudapp.azure.com/latest/
{"message":"KernelCI API"}
```

The interactive API documentation should also be available on
[https://kernelci-api.eastus.cloudapp.azure.com/latest/docs](https://kernelci-api.eastus.cloudapp.azure.com/latest/docs)

## Enjoy!

Now you have a publicly available API/Pipeline instance suitable for production.  To
make best use of it, you can try the `kci` command line tools to do things by
hand and run the `kernelci-pipeline` services to automatically run jobs, send
email reports etc.  See the main documentation for pointers with more details.

