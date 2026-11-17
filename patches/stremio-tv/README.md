Canonical implementation: `/Users/shre/Documents/projects/stremio-tv`, local
branch `bigscreen-bridge`, based on `605547b575ba2c20f791e96f8089ae7ff19a076d`.
The exported patch includes all phase 6 source/test/documentation changes and
excludes unrelated `.vscode` files. It has not been committed or pushed.

The native source, TV bundle and tests are described in
`docs/verification-phase6.md` in the bridge repository. Rebuild via the canonical
checkout's `native/packaging/build-bridge-flatpak.sh` on Linux, or the full
production Flatpak manifest. The bridge's `scripts/deploy-stremio.sh` copies this
checkout to a separate TV build source directory.
