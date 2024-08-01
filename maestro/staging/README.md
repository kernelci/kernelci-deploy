# Maestro Staging

This is new staging system related scripts and supplementary files.

# Principle of operation

The system is based on the following principles:

1. The system is based on the concept of creating staging-snapshot branches in several repositories using pending pull requests that qualify for staging.
2. System will build and deploy the staging-snapshot branches to the staging environment ( staging instances of kernelci-api, kernelci-pipeline ) which also use staging-prefixed docker images of cross-compile tools.
3. Based on results maintainers can approve or reject pending pull requests.

# How to use

Run staging.sh

# Files purpose

- staging.sh - main script
- staging-branch.py - Python script to create staging-snapshot branches, that pull PR, validate if they qualify for staging and create a staging-snapshot branch.
- users.txt - list of trusted users, PR will be tested only if the author is in this list.
