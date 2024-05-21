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
import sqlite3
from concurrent.futures import ThreadPoolExecutor

CONN_STRING=""
executor = ThreadPoolExecutor(max_workers=20)

def read_conn_string():
    global CONN_STRING
    # read from .azure_secret
    with open(".azure_secret", "r") as f:
        CONN_STRING = f.read().strip()
    return CONN_STRING

def opendb(dbfile):
    '''
    Open SQLite database
    '''
    # if file dont exist - init db
    if not os.path.exists(dbfile):
        #conn = sqlite3.connect(dbfile, check_same_thread=False)
        conn = sqlite3.connect(dbfile)
        c = conn.cursor()
        c.execute('''CREATE TABLE files
            (share text, directory text, filename text, creation_time text, last_write_time text, size int)''')
        c.execute('''CREATE TABLE directories
            (share text, directory text, creation_time text, last_write_time text)''')
        c.execute('''CREATE INDEX idx_dir ON directories(share, directory)''')
        c.execute('''CREATE INDEX idx_files ON files(share, directory, filename)''')
        conn.commit()
        conn.close()
    try:
        conn = sqlite3.connect(dbfile)
    except sqlite3.Error as e:
        print(e)
        return None
    conn.row_factory = sqlite3.Row
    return conn


def get_all_metadata(dbhandle):
    '''
    Get files metadata from SQLite database
    '''
    dbmetadata = {}
    c = dbhandle.cursor()
    c.execute("SELECT * FROM files")
    # Iterate over rows fetchone
    for row in c.fetchone():
        dbmetadata[row[0]] = row[1:]

    return dbmetadata


def get_metadata(share_client, directory_name):
    '''
    Get metadata for a directory
    '''
    directory_client = share_client.get_directory_client(directory_name)
    metadata = directory_client.get_directory_properties()
    return metadata

def db_add_directory(share_name, directory_name, creation_time, last_write_time, args):
    '''
    Add directory to SQLite database
    '''
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    c.execute("INSERT INTO directories VALUES (?,?,?,?)", (share_name, directory_name, creation_time, last_write_time))
    dbhandle.commit()

def db_get_directory(share_name, directory_name, args):
    '''
    Get directory from SQLite database
    '''
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    c.execute("SELECT * FROM directories WHERE share = ? AND directory = ?", (share_name, directory_name))
    row = c.fetchone()
    if row:
        return row
    return None

def db_add_directory(share_name, directory_name, creation_time, last_write_time, args):
    '''
    Add directory to SQLite database
    '''
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    c.execute("INSERT INTO directories VALUES (?,?,?,?)", (share_name, directory_name, creation_time, last_write_time))
    dbhandle.commit()

def db_add_file(share_name, directory_name, filename, creation_time, last_write_time, size, args):
    '''
    Add file to SQLite database
    '''
    print(f"Adding file {share_name}/{directory_name}/{filename} to db: {creation_time} {last_write_time} {size}")
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    c.execute("INSERT INTO files VALUES (?,?,?,?,?,?)", (share_name, directory_name, filename, creation_time, last_write_time, size))
    dbhandle.commit()

def db_get_file(share_name, directory_name, filename, args):
    '''
    Get file from SQLite database
    '''
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    c.execute("SELECT * FROM files WHERE share = ? AND directory = ? AND filename = ?", (share_name, directory_name, filename))
    row = c.fetchone()
    if row:
        return row
    return None

def db_get_olddirs(maxage, args):
    '''
    Get directories older than X days
    '''
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    c.execute("SELECT * FROM directories")
    rows = c.fetchall()
    old_dirs = []
    for row in rows:
        # check if older than X days
        try:
            creation_time = datetime.strptime(row['creation_time'], "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            print(f"Error parsing date {row['creation_time']}")
            continue
        if datetime.now() - creation_time > timedelta(days=maxage):
            diff_days = (datetime.now() - creation_time).days
            #print(f"Directory {row['share']}/{row['directory']} is older than {maxage} days, was created {creation_time} age {diff_days} days")
            #dirpath = f"{row['share']}/{row['directory']}"
            old_dirs.append(row)
    return old_dirs


def db_delete_directory(share_name, directory_name, args):
    # delete directory and files in this directory
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    # delete directory AND children directories
    c.execute("DELETE FROM directories WHERE share = ? AND directory LIKE ?", (share_name, f"{directory_name}%"))
    # delete files in this directory that match path (LIKE)
    c.execute("DELETE FROM files WHERE share = ? AND directory LIKE ?", (share_name, f"{directory_name}%"))


def enumerate_directory(share_service_client, share_name, args, path=''):
    '''
    Enumerate files in a directory
    '''
    print(f"Enumerating {share_name}/{path}")
    share_client = share_service_client.get_share_client(share_name)
    directory_client = share_client.get_directory_client(path)
    file_list = []
    for item in directory_client.list_directories_and_files():
        if item.is_directory:
            dbdir = db_get_directory(share_name, item.name, args)
            if not dbdir:
                # if not in db, insert into sqlite
                newpath = f"{item.name}"
                if len(path) > 0:
                    newpath = f"{path}/{item.name}"
                meta = get_metadata(share_client, newpath)
                db_add_directory(share_name, newpath, meta['creation_time'], meta['last_write_time'], args)
                print(f"{share_name},{item.name},{meta['creation_time']},{meta['last_write_time']} {newpath}")
                print(f"EnumeratingCallDir {newpath} from {path}")
                #enumerate_directory(share_service_client, share_name, args, newpath)
                executor.submit(enumerate_directory, share_service_client, share_name, args, newpath)
            else:
                print(f"Skipping {share_name}/{item.name} already in database")

        else:
            
            dbfileinfo = db_get_file(share_name, path, item.name, args)
            if not dbfileinfo:
                print(f"Add file: {item.name}")
                #db_add_file(share_name, path, item.name, item.last_modified, item.last_modified, item.size, args)
                executor.submit(db_add_file, share_name, path, item.name, item.creation_time, item.last_modified, item.size, args)
            else:
                print(f"Skipping file {share_name}/{path}/{item.name} already in database")

def delete_directory(share_service_client, share_name, directory_name, args):
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
            delete_directory(share_service_client, share_name, full_path, args)
        else:
            print(f"Deleting file {share_name}/{directory_name}/{item.name}")
            file_client = directory_client.get_file_client(item.name)
            file_client.delete_file()
    # delete directory
    directory_client.delete_directory()
    db_delete_directory(share_name, directory_name, args)


def delete_old(args):
    '''
    Delete files older than X days
    '''
    # retrieve directories from sqlite
    print(f"Retrieving directories older than {args.delete_older} days")
    old_dirs = db_get_olddirs(args.delete_older, args)
    print(f"Deleting directories older than {args.delete_older} days")
    share_service_client = ShareServiceClient.from_connection_string(CONN_STRING)
    for row in old_dirs:
        # is it root directory? must not have /
        if '/' in row['directory']:
            print(f"Skipping {row['share']}/{row['directory']} as it is not root directory")
            continue
        print(f"Deleting {row['share']}/{row['directory']}")
        #delete_directory(share_service_client, row['share'], row['directory'], args)
        executor.submit(delete_directory, share_service_client, row['share'], row['directory'], args)
        #db_delete_directory(row['share'], row['directory'], args)


def delete_files_by_pattern(pattern, args):
    '''
    Delete files by pattern
    '''
    # retrieve directories from sqlite
    share_service_client = ShareServiceClient.from_connection_string(CONN_STRING)
    dbhandle = args.dbhandle
    c = dbhandle.cursor()
    c.execute("SELECT * FROM files WHERE filename LIKE ?", (pattern,))
    rows = c.fetchall()
    for row in rows:
        print(f"Deleting {row['share']}/{row['directory']}/{row['filename']}")
        share_client = share_service_client.get_share_client(row['share'])
        directory_client = share_client.get_directory_client(row['directory'])
        file_client = directory_client.get_file_client(row['filename'])
        file_client.delete_file()
        # delete from database
        c.execute("DELETE FROM files WHERE share = ? AND directory = ? AND filename = ?", (row['share'], row['directory'], row['filename']))
        dbhandle.commit()



def main():
    '''
    Main function
    '''
    args = argparse.ArgumentParser()
    args.add_argument("--conn-string", help="Azure Storage connection string")
    # bool list-dirs
    args.add_argument("--updatelist", help="Update list of files", action="store_true")
    # delete older than X days
    args.add_argument("--delete-older", help="Delete files older than X days", type=int)
    # we need also csv file with list of directories to delete
    args.add_argument("--dbfile", help="SQLite file", default="azure_files.db")
    # delete by pattern
    args.add_argument("--delete-pattern", help="Delete files by pattern")
    # synchronous
    args.add_argument("--dbasync", help="Asynchronous mode", action="store_true")
    # journal_mode memory
    args.add_argument("--dbjournal-mode-memory", help="Set journal mode to memory", action="store_true")
    args = args.parse_args()
    if args.conn_string:
        connection_string = args.conn_string
    else:        
        connection_string = read_conn_string()        
    if not connection_string:
        print('AZURE_STORAGE_CONNECTION_STRING not set')
        sys.exit(1)
    share_service_client = ShareServiceClient.from_connection_string(connection_string)
    # if async or journal_mode_memory - backup database (copy to .bkp)
    if args.dbasync or args.dbjournal_mode_memory:
        os.system(f"cp {args.dbfile} {args.dbfile}.bkp")
    # open database
    args.dbhandle = opendb(args.dbfile)
    if not args.dbhandle:
        print("Cannot open database")
        sys.exit(1)

    # set journal mode to memory
    if args.dbjournal_mode_memory:
        c = args.dbhandle.cursor()
        c.execute("PRAGMA journal_mode = MEMORY")
        args.dbhandle.commit()
    
    # set asynchronous mode (pragma synchronous = off)
    if args.dbasync:
        c = args.dbhandle.cursor()
        c.execute("PRAGMA synchronous = OFF")
        args.dbhandle.commit()

    # delete by pattern (SQL like)
    if args.delete_pattern:
        print(f"Deleting files matching pattern {args.delete_pattern}")
        # delete files matching pattern
        delete_files_by_pattern(args.delete_pattern, args)

    # get list of all file shares
    if args.updatelist:
        file_shares = share_service_client.list_shares()
        print("Share,Directory,Creation Time,Size")
        for share in file_shares:
            enumerate_directory(share_service_client, share.name, args)

    if args.delete_older:
        delete_old(args)
    
    # close database
    args.dbhandle.close()
    # wait for all threads to finish
    executor.shutdown(wait=True)
    print("Done")
            

if __name__ == "__main__":
    main()

