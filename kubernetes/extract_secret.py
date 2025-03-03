#! /usr/bin/env python3
# Kubernetes secret extractor
# (c) 2025, Collabora Ltd.
# Author: Denys Fedoryshchenko <denys.f@collabora.com>

import yaml
import base64
import sys


def extract_secret(filename, secret_name, dstfile):
    with open(filename, 'r') as f:
        data = yaml.safe_load(f)
        encoded = data['data'][secret_name]
        decoded = base64.b64decode(encoded).decode('utf-8')
        with open(dstfile, 'w') as f:
            f.write(decoded)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: python3 {sys.argv[0]} <filename> <secret_name> <dstfile>")
        sys.exit(1)
    extract_secret(sys.argv[1], sys.argv[2], sys.argv[3])
