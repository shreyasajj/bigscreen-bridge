The canonical implementation lives in `/Users/shre/Documents/projects/couchside`
on the existing `ui-modernize` branch. The patch is an export of this phase's
changes against local revision `afd45818f60d76f7c0068d612d2a52e679fabddc`.
It includes the existing branch's theme dependencies; do not apply it to the
older upstream `main` audit revision without porting those dependencies.

Build from the canonical checkout using `scripts/deploy-couchside.sh` in the
bridge repository. Source is copied to a separate directory on the TV; existing
`~/couchside` and `~/cs-exp` trees are preserved. The patched Flatpak is installed
as the existing Couchside app ID, preserving its settings and Sendspin identity.
No commit, push, merge or branch switch has been performed in the local app repo.
