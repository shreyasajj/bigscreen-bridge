The canonical implementation is the `bigscreen-bridge` branch of the Couchside
repository. This patch is generated from it by `scripts/export-patches.py` against
the base revision in `config/repositories.json`; do not edit it by hand.

Build from the canonical checkout using `scripts/deploy-couchside.sh` in the
bridge repository. Source is copied to a separate directory on the TV; existing
`~/couchside` and `~/cs-exp` trees are preserved. The patched Flatpak is installed
as the existing Couchside app ID, preserving its settings and Sendspin identity.
