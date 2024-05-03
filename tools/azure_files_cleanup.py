#!/usr/bin/env python3
'''
Clean up older than 30 days files from Azure Files
'''

import os
import sys
import argparse
from azure.storage.fileshare import ShareServiceClient
from datetime import datetime, timedelta
import csv


CONN_STRING=""

def read_conn_string():
    global CONN_STRING
    # read from .azure_secret
    with open(".azure_secret", "r") as f:
        CONN_STRING = f.read().strip()
    return CONN_STRING

def get_metadata(share_client, directory_name):
    '''
    Get metadata for a directory
    '''
    directory_client = share_client.get_directory_client(directory_name)
    metadata = directory_client.get_directory_properties()
    return metadata

def enumerate_directories(share_service_client, share_name):
    '''
    Enumerate files in a directory
    '''
    share_client = share_service_client.get_share_client(share_name)
    directory_client = share_client.get_directory_client("")
    file_list = []
    for item in directory_client.list_directories_and_files():
        if item.is_directory:
            meta = get_metadata(share_client, item.name)
            print(f"{share_name},{item.name},{meta['creation_time']}")

def delete_directory(share_service_client, share_name, directory_name):
    '''
    Delete directory
    '''
    print(f"Deleting {share_name}/{directory_name}")
    share_client = share_service_client.get_share_client(share_name)
    directory_client = share_client.get_directory_client(directory_name)
    # verify if directory exists
    if not directory_client.exists():
        print(f"Directory {share_name}/{directory_name} does not exist")
        return
    # go over files and delete, if other directories, call recursively
    for item in directory_client.list_directories_and_files():
        if item.is_directory:
            full_path = f"{directory_name}/{item.name}"
            delete_directory(share_service_client, share_name, full_path)
        else:
            print(f"Deleting file {share_name}/{directory_name}/{item.name}")
            file_client = directory_client.get_file_client(item.name)
            file_client.delete_file()
    # delete directory
    directory_client.delete_directory()
    

def main():
    '''
    Main function
    '''
    args = argparse.ArgumentParser()
    args.add_argument("--conn-string", help="Azure Storage connection string")
    # bool list-dirs
    args.add_argument("--list-dirs", help="List directories", action="store_true")
    # delete older than X days
    args.add_argument("--delete-older", help="Delete files older than X days", type=int)
    # we need also csv file with list of directories to delete
    args.add_argument("--dirlist", help="CSV file with list of directories and metadata")
    args = args.parse_args()
    if args.conn_string:
        connection_string = args.conn_string
    else:        
        connection_string = read_conn_string()        
    if not connection_string:
        print('AZURE_STORAGE_CONNECTION_STRING not set')
        sys.exit(1)
    share_service_client = ShareServiceClient.from_connection_string(connection_string)
    # get list of all file shares
    if args.list_dirs:
        file_shares = share_service_client.list_shares()
        print("Share,Directory,Creation Time")
        for share in file_shares:
            enumerate_directories(share_service_client, share.name)

    # if delete_older set and dirlist not
    if args.delete_older and not args.dirlist:
        print("You need to specify --dirlist")
        sys.exit(1)

    if args.delete_older and args.dirlist:
        # load dirlist from file
        # Share,Directory,Creation Time
        with open(args.dirlist, "r") as f:
            reader = csv.reader(f)
            for row in reader:
                share_name = row[0]
                directory_name = row[1]
                creation_time = row[2]
                # convert to datetime
                # 2023-09-03 14:11:11.601250
                try:
                    creation_time = datetime.strptime(creation_time, "%Y-%m-%d %H:%M:%S.%f")
                except ValueError:
                    continue
                # check if older than X days
                if datetime.now() - creation_time > timedelta(days=args.delete_older):
                    delete_directory(share_service_client, share_name, directory_name)
                else:
                    print(f"Skipping {share_name}/{directory_name}")
            

if __name__ == "__main__":
    main()
