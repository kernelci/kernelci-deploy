## KernelCI rootfs "on-site" builder

This document describes the steps to build a KernelCI rootfs image locally

### Prerequisites

* Debian compatible Linux distribution with docker installed

### Quick start

```bash
./kci-rootfs.py --arch amd64,arm64 --name bullseye-gst-fluster --branch staging.kernelci.org
```

Builds amd64 and arm64 rootfs images for bullseye-gst-fluster (information retrieved from kernelci-core/config/core/rootfs-configs.yaml),
using kernelci-core branch staging.kernelci.org.

### Roadmap

* Upload results to KernelCI storage
* Update rootfs configs from kernelci-core