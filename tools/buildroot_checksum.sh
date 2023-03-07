#!/bin/sh
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Copyright (C) 2023, Collabora Ltd.
# Author: Denys Fedoryshchenko <denys.f@collabora.com>
#
set -e

# if write is 1, write to checksum file and optionally update commit in bootrr.mk if -b is set
write=0
# if latest_commit_branch is not empty, use latest commit from branch $latest_commit_branch
latest_commit_branch=""

while getopts "f:wb:h" opt; do
    case $opt in
        f)
            file=$OPTARG
            ;;
        # write to "checksum" file
        w)
            write=1
            ;;
        b)
            latest_commit_branch=$OPTARG
            ;;
        h)
            echo "Usage: $0 -f <path/to/bootrr.mk> [-w] [-l <branch>]"
            echo "  -f <path/to/bootrr.mk>  Path to bootrr.mk"
            echo "  -w                      Write to checksum file"
            echo "  -b <branch>             Use latest commit from branch"
            echo "  -h                      Print this help message"
            echo ""
            echo "This tool generates checksum for buildroot package from package.mk file,"
            echo "and optionally writes it to checksum file if -w is set."
            echo "It can also update commit in bootrr.mk file to latest if -b [branchname]"
            echo "is set."
            exit 0
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
        :)
            echo "Option -$OPTARG requires an argument." >&2
            exit 1
            ;;
    esac
done

if [ -z "$file" ]; then
    echo "Option -f is required, usually path/buildroot/package/bootrr/bootrr.mk" >&2
    exit 1
fi

url=$(grep -oP '(?<=_SITE = ).*' $file)
commit=$(grep -oP '(?<=_VERSION = ).*' $file)
package=$(grep -oP '(?<=_SITE = ).*' $file | sed 's/.*\/\(.*\)\.git/\1/')

# if latest_commit, retrieve latest commit from branch
if [ -n "$latest_commit_branch" ]; then
    echo "Using latest commit from branch $latest_commit_branch"
    commit=$(git ls-remote $url $latest_commit_branch | cut -f1)
fi

echo "Generating checksum for $package from $url at $commit"

if [ -z "$url" ] || [ -z "$commit" ] || [ -z "$package" ]; then
    echo "url or commit undefined" >&2
    exit 1
fi

tmpdir=$(mktemp -d)
mkdir -p $tmpdir/package
cd $tmpdir/package

output="../${package}-${commit}.tar.gz"

# quiet clone
git clone --quiet ${url} ${package}

# this is buildroot specific, but it's the only way to get the checksum
cd "${package}"
basename="${package}-${commit}"

git checkout -f -q ${commit}
git clean -ffdx

date="$( git log -1 --pretty=format:%cD )"

find . -not -type d \
       -and -not -path "./.git/*" >"${output}.list"
LC_ALL=C sort <"${output}.list" >"${output}.list.sorted"

# Create GNU-format tarballs, since that's the format of the tarballs on
# sources.buildroot.org and used in the *.hash files
tar cf - --transform="s#^\./#${basename}/#" \
         --numeric-owner --owner=0 --group=0 --mtime="${date}" --format=gnu \
         -T "${output}.list.sorted" >"${output}.tar"
gzip -6 -n <"${output}.tar" >"${output}"

rm -f "${output}.list"
rm -f "${output}.list.sorted"
cd ..
sha256sum ${package}-${commit}.tar.gz > ${package}-${commit}.tar.gz.sha256

# append data from .sha256
if [ $write -eq 1 ]; then
    echo "Writing to ${file%.*}.hash"
    echo "# Locally generated" > ${file%.*}.hash
    echo "sha256 `cat ${package}-${commit}.tar.gz.sha256`" >> ${file%.*}.hash
    # if latest_commit, update commit in bootrr.mk
    # update PACKAGE_VERSION = with latest commit
    if [ -n "$latest_commit_branch" ]; then
        echo "Updating commit in $file"
        sed -i "s/_VERSION = .*/_VERSION = ${commit}/" $file
    fi
else
    echo "# Locally generated\nsha256 `cat ${package}-${commit}.tar.gz.sha256`"
fi

rm -rf $tmpdir
exit 0
