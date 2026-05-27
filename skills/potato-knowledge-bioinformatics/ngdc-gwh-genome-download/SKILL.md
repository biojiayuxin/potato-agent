---
name: ngdc-gwh-genome-download
version: 1.0.0
description: Download genome assembly files from CNCB-NGDC Genome Warehouse (GWH) when the user provides an NGDC BioProject, BioSample, GWH accession, GWH assembly page, or direct NGDC download context.
metadata:
  hermes:
    tags: [NGDC, CNCB, GWH, genome-download, FASTA, GFF, annotation, potato]
required_commands:
  - python3
---

# NGDC/GWH Genome Download

Use this skill to download genome assembly files from **CNCB-NGDC Genome Warehouse (GWH)**.

## Use When

- The user provides an NGDC BioProject accession such as `PRJCA...`.
- The user provides an NGDC BioSample accession and wants linked genome files.
- The user provides a GWH accession, GWH assembly page, or `download.cncb.ac.cn/gwh/...` URL.
- The user asks to download genome FASTA, GFF/GTF, RNA/CDS, or protein files from NGDC/GWH.

Do not use this skill for raw-read-only GSA downloads or non-NGDC repositories.

## Inputs

Ask for a destination directory if the user did not provide one. Preserve upstream filenames unless the user requests local renaming.

Accepted identifiers:

- BioProject: `PRJCA...`
- BioSample: `SAMC...`
- GWH assembly accession: usually `GWH...00000000`
- GWH assembly page: `https://ngdc.cncb.ac.cn/gwh/Assembly/.../show`
- GWH download URL: `https://download.cncb.ac.cn/gwh/...`

## Workflow

1. **Resolve NGDC record**
   - For BioProject: open or query `https://ngdc.cncb.ac.cn/bioproject/browse/<PRJCA>`.
   - For BioSample: open or query `https://ngdc.cncb.ac.cn/biosample/browse/<SAMC>` and identify the linked BioProject.
   - For GWH page/download URL: extract the GWH accession and download directory.

2. **Query BioProject to GWH assemblies**
   Static BioProject HTML may omit GWH rows. Query the GWH AJAX endpoint:

   ```bash
   python3 - <<'PY'
   import json
   import urllib.request

   prj = 'PRJCA000000'
   url = f'https://ngdc.cncb.ac.cn/gwh/gsa/ajax/getAssembliesListByBioProjectAccession/{prj}'
   req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
   with urllib.request.urlopen(req, timeout=30) as resp:
       data = json.load(resp)
   print(json.dumps(data, ensure_ascii=False, indent=2))
   PY
   ```

   Expected useful fields:
   - `count`
   - `assembliesList[].accession` (GWH assembly accession)
   - `assembliesList[].hyperlink` (assembly page)
   - `assembliesList[].downloadLinks.downloadLinksList[]`

3. **Select files**
   Match requested file types to GWH labels:

   - `DNA`: genome FASTA
   - `GFF`: gene annotation
   - `RNA`: transcript FASTA
   - `Protein`: protein FASTA

   If an expected file type is absent, verify the assembly page and download directory before reporting it unavailable. Do not invent URLs from filename patterns.

4. **Download**
   Prefer resumable downloads:

   ```bash
   mkdir -p "$DEST"
   cd "$DEST"
   wget -c --tries=5 --timeout=30 "$DNA_URL"
   wget -c --tries=5 --timeout=30 "$GFF_URL"
   ```

   If `wget` is unavailable, use:

   ```bash
   curl -L -C - --retry 5 --connect-timeout 30 -O "$DNA_URL"
   curl -L -C - --retry 5 --connect-timeout 30 -O "$GFF_URL"
   ```

5. **Validate**

   Always run integrity checks before reporting success:

   ```bash
   gzip -t *.gz
   sha256sum *.gz
   ls -lh
   ```

6. **Document**
   Write a compact README in the destination directory with:

   - NGDC BioProject/BioSample URL when used
   - GWH assembly accession and page
   - downloaded filenames, file type, size, and SHA256
   - original download URLs
   - exact re-download commands
   - validation commands and result

## Pitfalls

- Do not rely only on static BioProject HTML; use the AJAX endpoint for GWH rows.
- GSA `CRA...` records are raw reads, not genome assembly files.
- Report missing annotation only after checking `downloadLinksList`, the assembly page, and the download directory when accessible.
- Preserve upstream filenames unless the user requests renaming.
- Keep local labels separate from official NGDC/GWH accessions.
