#!/usr/bin/env python3
#

import os

res = os.system("./1-rebuild_all.sh")
if res != 0:
    exit(1)

res = os.system("./2-install_api.sh")
if res != 0:
    exit(1)

res = os.system("./3-install_pipeline.sh")
if res != 0:
    exit(1)

res = os.system("./4-start-cycle.sh")
if res != 0:
    exit(1)
