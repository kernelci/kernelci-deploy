#!/usr/bin/env python3
#

import os

os.system("./1-rebuild_all.sh")
os.system("./2-install_api.sh")
os.system("./3-install_pipeline.sh")
os.system("./4-start-cycle.sh")
