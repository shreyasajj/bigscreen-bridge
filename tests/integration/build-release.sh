#!/bin/sh
# Build a release of the working tree the way release.yml does, into dist/, plus a
# `releases/latest` file in the GitHub API's shape, so install.sh can install it with
#   BIGSCREEN_RELEASES_API=file://<root>/releases sh install.sh
# Usage: sh tests/integration/build-release.sh [URL root the container will see; default: $PWD/dist]
set -eu
root=${1:-$(pwd)/dist}
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)
name="bigscreen-bridge-$version"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
rm -rf dist && mkdir -p dist/releases
# The working tree, not HEAD: a test run tests what is checked out, edits included.
git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf - | (mkdir -p "$stage/$name" && tar -xf - -C "$stage/$name")
tar -czf "dist/$name.tar.gz" -C "$stage" "$name"
cp install.sh dist/install.sh
(cd dist && { sha256sum "$name.tar.gz" install.sh 2>/dev/null || shasum -a 256 "$name.tar.gz" install.sh; } > SHA256SUMS)
cat > dist/releases/latest <<JSON
{"tag_name": "v$version", "draft": false, "prerelease": false, "assets": [
  {"name": "$name.tar.gz", "browser_download_url": "file://$root/$name.tar.gz"},
  {"name": "install.sh", "browser_download_url": "file://$root/install.sh"},
  {"name": "SHA256SUMS", "browser_download_url": "file://$root/SHA256SUMS"}]}
JSON
echo "dist/: $name.tar.gz install.sh SHA256SUMS releases/latest"
