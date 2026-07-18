# Hermes downstream patches

`series` is the authoritative application order, and must exactly match
`upstream.yaml`'s `patch_series`. An empty series is valid. A declared patch
that is missing, undeclared, duplicated, unsafe, or cannot pass `git apply
--check` stops vendoring without producing an output tree.

For a production release, the exact ordered patch bytes are also bound into the
schema-v2 replay attestation in `upstream.yaml`. The attested replay statement
must name the fully patched Git tree, and that tree must exactly match the
committed `hermes-agent` subtree used by the builder. Merely changing
`provenance_status` does not satisfy the release gate.

Each future patch must document its purpose, changed files, behavior contract,
tests, rollback, upstream replacement status, and deletion condition in this
directory. Patch application is intentionally separate from production
deployment: the vendor command only writes an explicit output path outside
`/opt` and `/srv`.
