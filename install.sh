#!/bin/sh
# Install Bigscreen Bridge on a Plasma Bigscreen TV, as the TV's desktop user:
#
#   curl -fsSL https://github.com/shreyasajj/bigscreen-bridge/releases/latest/download/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --prerelease        follow pre-releases (security fixes first)
#   curl -fsSL .../install.sh | sh -s -- --version 0.3.0     a specific release
#   curl -fsSL .../install.sh | sh -s -- --no-auto-update    no nightly updates
#   curl -fsSL .../install.sh | sh -s -- --stable            back to stable releases only
#
# Downloads the release, checks it against the release's SHA256SUMS, unpacks it to
# ~/.local/share/bigscreen-bridge/releases/<version> and runs its scripts/install-tv.sh.
# Re-running upgrades in place; the config, token and paired clients are kept.
set -eu
repo=shreyasajj/bigscreen-bridge
api="${BIGSCREEN_RELEASES_API:-https://api.github.com/repos/$repo/releases}"   # override for tests
mode=latest
version=
auto=
channel=
while [ $# -gt 0 ]; do
    case "$1" in
        --prerelease) mode=prerelease; channel=prerelease ;;
        --version) [ $# -ge 2 ] || { echo "--version needs a value" >&2; exit 2; }; mode=tag; version=${2#v}; shift ;;
        --stable) channel=stable ;;
        --no-auto-update) auto=0 ;;
        -h|--help) sed -n '2,12p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
for tool in curl tar python3 sha256sum systemctl; do
    command -v "$tool" >/dev/null 2>&1 || { echo "install.sh needs $tool" >&2; exit 1; }
done

case "$mode" in
    latest) url="$api/latest" ;;
    tag) url="$api/tags/v$version" ;;
    prerelease) url="$api?per_page=30" ;;
esac
# The newest matching release's version and asset URLs, one per line.
release=$(curl -fsSL -H 'Accept: application/vnd.github+json' "$url" | python3 -c '
import json, sys
data = json.load(sys.stdin)
releases = data if isinstance(data, list) else [data]
release = next((r for r in releases if not r.get("draft")), None)
if release is None:
    sys.exit("No published release found")
version = release["tag_name"].lstrip("v")
assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
name = "bigscreen-bridge-%s.tar.gz" % version
if name not in assets or "SHA256SUMS" not in assets:
    sys.exit("Release %s has no %s or SHA256SUMS" % (version, name))
print(version); print(assets[name]); print(assets["SHA256SUMS"])
')
version=$(echo "$release" | sed -n 1p)
archive_url=$(echo "$release" | sed -n 2p)
sums_url=$(echo "$release" | sed -n 3p)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
archive="bigscreen-bridge-$version.tar.gz"
echo "Downloading Bigscreen Bridge $version"
curl -fsSL -o "$work/$archive" "$archive_url"
curl -fsSL -o "$work/SHA256SUMS" "$sums_url"
(cd "$work" && grep "  $archive\$" SHA256SUMS > wanted.sums && sha256sum -c wanted.sums > /dev/null 2>&1) \
    || { echo "Checksum mismatch for $archive; nothing was installed" >&2; exit 1; }

dest="$HOME/.local/share/bigscreen-bridge/releases/$version"
mkdir -p "$work/unpacked"
tar -xzf "$work/$archive" -C "$work/unpacked" --strip-components=1 --no-same-owner
rm -rf "$dest"
mkdir -p "$(dirname "$dest")"
mv "$work/unpacked" "$dest"

BIGSCREEN_UPDATE_CHANNEL=$channel BIGSCREEN_AUTO_UPDATE=$auto sh "$dest/scripts/install-tv.sh"
