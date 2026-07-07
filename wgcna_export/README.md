# WGCNA Co-expression Export

This directory contains the offline export pipeline for the WGCNA network viewer
served at `/wgcna`.

The source WGCNA result directory is read-only; generated tables default to:

```bash
$HOME/tmp/wgcna_coexpression_export
```

## Export Tables

Run from the repository root:

```bash
/opt/interface-env/bin/python wgcna_export/scripts/export_network_metadata.py
/opt/interface-env/bin/python wgcna_export/scripts/export_gene_module_tables.py
/opt/interface-env/bin/python wgcna_export/scripts/compute_module_overlaps.py
Rscript wgcna_export/scripts/export_tom_top_edges.R \
  --base-dir /mnt/data/potato_agent/work/WGCNA/03-network \
  --output-dir "$HOME/tmp/wgcna_coexpression_export" \
  --networks leaf,stem,root,reproductive,tuberization \
  --top-n 100
/opt/interface-env/bin/python wgcna_export/scripts/compute_shared_edges.py
/opt/interface-env/bin/python wgcna_export/scripts/validate_exports.py
```

Use a different output location with:

```bash
WGCNA_EXPORT_DIR=/path/to/export /opt/interface-env/bin/python wgcna_export/scripts/export_network_metadata.py
```

## Production Data Layout

Do not store WGCNA runtime data in this Git checkout. Production snapshots live
under `/srv/wgcna_data`:

```text
/srv/wgcna_data/
  current -> releases/YYYYMMDD
  releases/
    YYYYMMDD/
      tables/
      logs/
```

Publish a finished export with:

```bash
release=/srv/wgcna_data/releases/$(date +%Y%m%d)
mkdir -p "$release"
rsync -a --delete "$HOME/tmp/wgcna_coexpression_export/" "$release/"
chown -R root:potato-interface /srv/wgcna_data
find /srv/wgcna_data -type d -exec chmod 0750 {} +
find /srv/wgcna_data -type f -exec chmod 0640 {} +
ln -sfn "$release" /srv/wgcna_data/current
chown -h root:potato-interface /srv/wgcna_data/current
```

The deployed interface reads PostgreSQL at request time. The TSV snapshot is kept
as the reproducible source used to rebuild or reload that database.

## Load PostgreSQL

Create the PostgreSQL database outside this repository, then load the exported TSVs:

```bash
export WGCNA_DATABASE_URL='postgresql://user:password@host:5432/wgcna'
/opt/interface-env/bin/python wgcna_export/scripts/load_to_postgresql.py --truncate
```

The current production convention uses peer auth for the `potato-interface` role:

```bash
sudo -u potato-interface env \
  WGCNA_EXPORT_DIR=/srv/wgcna_data/current \
  WGCNA_DATABASE_URL='postgresql:///potato_wgcna?host=/var/run/postgresql' \
  /opt/interface-env/bin/python /srv/potato_agent/wgcna_export/scripts/load_to_postgresql.py --truncate
```

The interface reads the same `WGCNA_DATABASE_URL` at runtime. If it is not set,
`/wgcna` still loads, but `/api/wgcna/*` returns HTTP 503.
