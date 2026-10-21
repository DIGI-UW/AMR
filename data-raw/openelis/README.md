# OpenELIS AMR reference distribution

This directory builds a standalone, versioned configuration ZIP from a pinned
commit of AMR for R. Deployments select the release and checksum. The deployment installs its CSV domain files into the existing configuration
folder; OpenELIS loads those files through its normal configuration handlers; it does not need R, this
repository, a submodule, or a copy of the reference data in its application image.

## First release scope

The baseline explicitly selects **EUCAST 2025 and EUCAST 2026**. It never chooses
a guideline by the current date or an unqualified “latest” setting. CLSI,
veterinary standards, ECOFFs and expert rules are outside this distribution.

The five organism concepts in `scope.json` are _Escherichia coli_, _Klebsiella
pneumoniae_, _Pseudomonas aeruginosa_, _Staphylococcus aureus_ and _Streptococcus
pneumoniae_. Antimicrobial concepts are included for all their candidate contexts,
including contexts whose breakpoints cannot be translated. This is a first
bounded baseline, not the full organism catalogue or complete EUCAST tables.

Only unconditional numeric MIC S/I/R rules are emitted. The exporter resolves
exact organisms, their taxonomic ancestors, AMR groups and broad taxa using the
pinned source, then compares each proposed rule with AMR's own `as.sir()` output.
The following contexts are excluded and reported instead of approximated:

- Disk diffusion, which requires disk-content context.
- Site, administration-route or urinary-tract conditions.
- Susceptible-dose-dependent categories, missing/blocking thresholds, no
  susceptible category, ambiguous equally ranked rules and conditional notes.
- Any rule whose translated interpretation disagrees with AMR.

A contextual candidate excludes its whole organism/antimicrobial/method context;
the exporter does not select a convenient numeric fallback. Notes are treated
conservatively: every nonempty note requires review before support can expand.
At the initial source pin there are **68 rules across the two editions**, for
four of the five organisms. _P. aeruginosa_ is a concept only; all its candidate
breakpoints are excluded. `coverage.csv` is the authoritative supported-context
list. This release alone is not a complete PNG testing catalogue.

EUCAST MIC boundaries are preserved exactly: S at or below `susceptible_value`,
R strictly above `resistant_value`, otherwise I. Thresholds are never moved to
the next dilution. Capped observations such as `<=0.5` require separate consumer
support and are not covered by the comparison cases.

## Build and validate

Prerequisites: Git, Python **3.12+**, and Docker with the selected context running.
The official path uses a digest-pinned R 4.5.1 image. Docker may pull that image
on first use; both source installation and export containers run without a
network. No third-party Python or R build packages are required.

From this repository root:

```sh
python3 data-raw/openelis/build_package.py \
  --source-ref 5fc152ae947240b03dd1085fb4ff1bc0df3137a1 \
  --package-version 2026.1 \
  --runtime docker \
  --output dist/amr-reference-2026.1.zip

AMR_REFERENCE_ARTIFACT=dist/amr-reference-2026.1.zip \
  python3 -m unittest discover -s data-raw/openelis -p 'test_*.py' -v

python3 data-raw/openelis/validate_package.py dist/amr-reference-2026.1.zip
```

The source commit must exist locally and must be its full 40-character ID.
Moving refs such as `main` are rejected. Source installation uses an isolated
library, so an already installed AMR package cannot substitute different data.
`--runtime local` is a development convenience for machines with R and Rscript;
release builds use the fixed Docker runtime.

Reproducibility includes file order, UTF-8 CSVs, canonical manifest JSON, fixed ZIP
permissions/timestamps and no build timestamp. Build into two different output
directories and compare the ZIP bytes. Rebuilding an existing path is allowed
only if its content is identical. Changed data, scope or exporter behaviour
requires a new distribution version. Historical releases remain available.

## Archive contract: `openelis-configuration/v1`

This is the generic metadata distribution layout, not an application ZIP import
API. `manifest.json` is at the root. The three operational CSVs are under:

```text
configuration/backend/microbiology-organisms/organisms.csv
configuration/backend/microbiology-antimicrobials/antimicrobials.csv
configuration/backend/microbiology-breakpoints/breakpoints.csv
```

All other payloads below are under `evidence/`. The manifest's `files` inventory
uses full archive-relative paths, with SHA-256 and byte length for every payload.
Its stable `packageId` is `amr-reference`; `packageVersion` identifies the release.
A generic deployment installer only needs this envelope and configuration paths.
AMR-specific scope, coverage and source interpretation checks remain in this
exporter's validator and the receiving application's compatibility tests.
No executable code is installed.

| File                         | Purpose                                                                                                                                                                                   |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `manifest.json`              | Format/package version, source commit and R-package version, source dataset hashes, exporter hashes, scope, edition identities and every payload's byte length, SHA-256 and CSV row count |
| `scope.json`                 | Explicit organism IDs, method and both guideline years                                                                                                                                    |
| `organisms.csv`              | `source,code,displayName,whonetCode,gramStain`                                                                                                                                            |
| `antimicrobials.csv`         | `source,code,displayName,whonetCode,antibioticClass`                                                                                                                                      |
| `breakpoints.csv`            | Exact organism/antimicrobial MIC rules with source IDs and strictness; see below                                                                                                          |
| `interpretation-vectors.csv` | Numeric boundary and off-dilution observations, with expected S/I/R generated by the pinned AMR interpreter                                                                               |
| `coverage.csv`               | Every candidate context for each selected organism: SUPPORTED or UNSUPPORTED, reason and contributing source row numbers                                                                  |
| `source-rows.csv`            | Every human breakpoint row in the two selected editions, linked to coverage or explicitly outside organism scope                                                                          |
| `NOTICE.txt`                 | Source and publisher attribution, source link, citation and the pinned source's full licence text                                                                                         |

Breakpoint columns are:

```text
publisher,version,organism_or_group,antibiotic_whonet_code,method,specimen_type_id,
breakpoint_type,susceptible_value,intermediate_lower_value,intermediate_upper_value,
resistant_value,units,resistant_inclusive,organism_source,organism_code,
antibiotic_source,antibiotic_code,notes
```

`source=AMR` plus `code` is the stable concept identity. WHONET codes and display
names are matching aids, not replacement IDs. The organism WHONET code is populated
only when the pinned source gives one unambiguous exact-name mapping. Group
rules are resolved into the selected exact organism; this package does not
introduce a shared group membership or broad fallback rule in OpenELIS.

Each edition has a package-specific version, e.g. `2026-2026.1`, so a corrected
release can coexist with historical results. Reusing that edition identity for changed configuration content must be rejected
by the consumer. Changing unrelated archive evidence does not change an edition.

## Publish and pin

The `OpenELIS reference distribution` workflow builds twice, compares the bytes,
runs contract/failure tests and retains candidate ZIP/checksum files on PRs.
It does not publish automatically. After merge, run the workflow on **main**
with an explicit source commit and distribution version, and enable `publish`.
It creates a release tagged `openelis-reference-<version>` with:

```text
amr-reference-<version>.zip
amr-reference-<version>.zip.sha256
```

An existing release cannot be overwritten by this workflow. The release tag
identifies the exporter revision; `manifest.source.commit` identifies the AMR
data revision. Pin the release URL **and the reviewed ZIP SHA-256** in the
deployment's configuration. Do not fetch from a `latest` URL at application boot.
Generated archives are release assets, not checked-in data or Liquibase seeds.

## Consumer requirements and deployment handoff

The receiving OpenELIS change adds the missing organism, antimicrobial and
breakpoint CSV domains to its existing catalog framework. It does not add an
archive parser or a second import pipeline. Verify the receiving application
supports source identity matching and strict resistant boundaries before pinning
this dataset.

1. Pin the release HTTPS URL, reviewed SHA-256, `id: amr-reference` and version
   in deployment configuration. The generic PNG initializer accepts any metadata
   publisher using `openelis-configuration/v1`; it has no AMR URL or filename
   assumptions.
2. On normal startup, the initializer verifies and caches the archive, checks its
   inventory, and installs the operational files into the existing configuration
   tree. It prefixes filenames with package identity and version to preserve site
   files and earlier editions. Evidence stays in the cache, outside scanned
   domains. No manual CSV upload is needed.
3. Existing OE2 startup/reload loads concepts before breakpoints. Import Catalog
   also exposes preview and apply with ordinary row summaries. A failed
   breakpoint file rolls back; earlier successful concept files can remain.
   New editions remain inactive. Observe import results, not just HTTP health.
4. Receiving-application compatibility tests load these same CSVs and compare
   every source-generated observation with the Java interpreter. Evidence files
   are test inputs, not runtime startup requirements.
5. Review coverage and explicitly activate an edition through administration.
   Missing exact supported rules must not acquire an inferred broad fallback.

Reloads preserve source identities, local edits, activation and historical IDs.
New revisions use new edition identities and load inactive. The deployment owns
metadata selection independently of application upgrades. No operational
concepts are seeded through Liquibase, stored in OE2 source, or supplied through
an R runtime in the laboratory application.
