The canonical implementation is the `bigscreen-bridge` branch of the Flimmer
checkout named in `config/repositories.json`. This patch is generated from it by
`scripts/export-patches.py`; do not edit it by hand.

The branch sits on top of the multi-source, Xtream and Stalker commits, which are
not in upstream `main` yet, so the patch does **not** apply to upstream `main` on its
own. Its base is the `base_revision` recorded in `config/repositories.json`.
