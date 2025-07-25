#!/usr/bin/env python3

import os
import sys
import argparse
import toml
import datetime
from dateutil import parser as date_parser
from azure.storage.blob import BlobServiceClient, ContainerClient

# vmlinux - keep 2 weeks
# lava_callback.json.gz - 1 month
DELETE_POLICY = [
    ("vmlinux", 14),
    ("lava_callback.json.gz", 30)
]

def load_credentials(config_file):
    config = toml.load(config_file)
    return config["azure"]["account"], config["azure"]["key"]


def retrieve_containers(blob_service_client):
    containers = []
    try:
        container_list = blob_service_client.list_containers()
        for container in container_list:
            containers.append(container.name)
    except Exception as e:
        print(f"Error retrieving containers: {e}")
        sys.exit(1)
    return containers


def process_blobs(container_client):
    try:
        blobs = container_client.list_blobs()
        for blob in blobs:
            print(f"Processing blob: {blob.name} created on {blob.creation_time}")
            age = datetime.datetime.now(datetime.timezone.utc) - blob.creation_time
            for pattern, days in DELETE_POLICY:
                if pattern in blob.name and age.days > days:
                    print(f"Deleting blob: {blob.name} (age: {age.days} days)")
                    # TODO: Send over queue or save for deferred/parallelized deletion
                    break
    except Exception as e:
        print(f"Error processing blobs: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Azure Blob Storage Cleanup")
    parser.add_argument("--config", required=True, help="Path to the config file")
    parser.add_argument("--container", required=True, help="Name of the container to clean up")
    args = parser.parse_args()

    storage_account, storage_key = load_credentials(args.config)
    blob_service_client = BlobServiceClient(account_url=f"https://{storage_account}.blob.core.windows.net", credential=storage_key)
    containers = retrieve_containers(blob_service_client)
    if not args.container or not args.container in containers:
        print(f"Container '{args.container}' not found. Available containers:")
        for container in containers:
            print(f"- {container}")
        sys.exit(1)

    container_client = blob_service_client.get_container_client(args.container)
    process_blobs(container_client)



if __name__ == "__main__":
    main()