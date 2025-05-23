#!/bin/bash
# List of target contexts
PROJECT=android-kernelci-external
# GCE clusters
declare -A CLUSTERS=(
  [gke_android-kernelci-external_europe-west1-d_kci-eu-west1]=europe-west1-d
  [gke_android-kernelci-external_europe-west4-c_kci-eu-west4]=europe-west4-c
  [gke_android-kernelci-external_us-central1-c_kci-us-central1]=us-central1-c
  [gke_android-kernelci-external_us-west1-a_kci-us-west1]=us-west1-a
  [gke_android-kernelci-external_us-east4-c_kci-big-us-east4]=us-east4-c
)

# List of target contexts
declare -a CONTEXTS=(
  gke_android-kernelci-external_europe-west1-d_kci-eu-west1
  gke_android-kernelci-external_europe-west4-c_kci-eu-west4
  gke_android-kernelci-external_us-central1-c_kci-us-central1
  gke_android-kernelci-external_us-west1-a_kci-us-west1
  gke_android-kernelci-external_us-east4-c_kci-big-us-east4
  aks-kbuild-medium-1
)

# if argument fetch
if [ "$1" == "fetch" ]; then
  echo "Fetching files from all clusters…"
  for CTX in "${!CLUSTERS[@]}"; do
    ZONE="${CLUSTERS[$CTX]}"
    echo "Triggering job for context: $CTX"
    kubectl --context="$CTX" create job \
      --from=cronjob/file-sync \
      "file-sync-manual-$(date +%s)"  
  done
  exit 0
fi

# Uncomment and run once
#echo "Enabling GCP Filestore CSI Driver on all clusters"
#declare -A ZONES=(
#  [kci-eu-west1]=europe-west1-d
#  [kci-eu-west4]=europe-west4-c
#  [kci-us-central1]=us-central1-c
#  [kci-us-west1]=us-west1-a
#  [kci-big-us-east4]=us-east4-c
#)

#for CL in "${!ZONES[@]}"; do
#  gcloud container clusters update "$CL" \
#   --project "$PROJECT" \
#    --zone "${ZONES[$CL]}" \
#   --update-addons=GcpFilestoreCsiDriver=ENABLED
#done


echo "Deploying to all clusters filestore-pvc.yaml"


for CTX in "${!CLUSTERS[@]}"; do
  ZONE="${CLUSTERS[$CTX]}"

  echo "Deleting old PVC and SC from $CTX (zone $ZONE)…"
  kubectl --context="$CTX" delete pvc shared-data --ignore-not-found
  kubectl --context="$CTX" delete sc filestore-rwx --ignore-not-found  

  echo "Applying PVC to $CTX (zone $ZONE)…"
  #sed "s/^\(\s*location:\).*/\1 ${ZONE}/" filestore-pvc.yaml \
  #  | kubectl --context="$CTX" apply -f -

  kubectl --context="$CTX" apply -f filestore-pvc.yaml
done

echo "Deploying to all clusters azurefile-pvc.yaml"
# Azure
kubectl --context=aks-kbuild-medium-1 apply -f azurefile-pvc.yaml

# Apply the manifest to each cluster
echo "Deploying to all clusters croncache.yaml"
for ctx in "${CONTEXTS[@]}"; do
  echo "Deploying to context: $ctx"
  kubectl --context="$ctx" apply -f croncache.yaml
done
