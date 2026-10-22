# OpenELIS AMR configuration packages

This exporter turns pinned AMR-for-R reference data into independently released
configuration files. GHCR stores the package. A deployment pins its digest and
places the files in OpenELIS's existing configuration folder. OpenELIS uses its
normal catalog handlers; it needs neither R nor a package archive importer.

## Scope and interpretation

The scope is **human EUCAST 2025/2026 and CLSI 2025/2026**, built separately.
There is no rolling latest-year selection. Veterinary standards, epidemiological
cutoffs and expert rules are outside this release.

`human-breakpoint-taxa` includes every organism target in the selected source
editions, plus accepted species of common human prevalence beneath those targets that
have WHONET mappings in the source.
Taxonomic ancestry and group membership come from the same pinned AMR revision.
All antimicrobial concepts referenced by candidate contexts are included, even
when a particular context cannot produce an interpretation. WHONET mappings use
unambiguous exact source mappings; missing mappings are left blank.

Each organism/agent/method/edition context receives an exact organism rule.
Supported MIC rules retain the original thresholds. EUCAST uses a strict
resistant boundary; CLSI uses an inclusive boundary and rounds the measured MIC
up to the conventional dilution ladder for interpretation. The measured value
itself is preserved by OpenELIS. Proposed numeric results are checked against
AMR's actual `as.sir()` using its normal source data and matching logic.

The exporter writes explicit empty rules for unsupported contexts. These return
**No breakpoint** and prevent a broad numeric fallback. Coverage records explain:

- Disk diffusion, until the receiving workflow can enforce disk content.
- Site, route, urinary-tract conditions and conditional notes.
- Susceptible-dose-dependent, incomplete and no-susceptible-category rules.
- Ambiguous equally ranked source rules or disagreement with source interpretation.

Nonempty notes are treated conservatively. Unsupported cases are accounted for,
not converted to convenient clinical thresholds. `coverage.csv` is the exact
supported/blocked inventory. This is a testing reference package, not approval
of every PNG clinical workflow. Capped observations and expert-rule logic require
separate receiving support.

## Build and validate

Use Git, Python 3.12+ and Docker. The builder installs the exact source revision
in an isolated library using a digest-pinned R image, without container network
access or third-party R/Python dependencies.

```sh
python3 data-raw/openelis/build_package.py \
  --source-ref 5fc152ae947240b03dd1085fb4ff1bc0df3137a1 \
  --package-version 2026.1 --runtime docker \
  --output dist/amr-reference-eucast-2026.1.zip

# Build CLSI separately; keep its output restricted.
python3 data-raw/openelis/build_package.py \
  --scope data-raw/openelis/scope-clsi.json \
  --source-ref 5fc152ae947240b03dd1085fb4ff1bc0df3137a1 \
  --package-version 2026.1 --runtime docker \
  --output dist/amr-reference-clsi-2026.1.zip

AMR_REFERENCE_ARTIFACT=dist/amr-reference-eucast-2026.1.zip \
  python3 -m unittest discover -s data-raw/openelis -p 'test_*.py' -v
```

Run the artifact tests for each publisher. Source refs must be full commit IDs.
`--runtime local` is a development convenience; release builds use pinned Docker.
Build to two different paths and compare bytes. Stable ordering, fixed ZIP times
and permissions, canonical JSON and no build timestamp make output reproducible.
A changed dataset, scope or exporter requires a new package version. Never replace
an existing release. New edition identities preserve earlier result references.

## Package layout

The generic envelope is `openelis-configuration/v1`. `manifest.json` records
package identity/version, source commit, dataset and exporter checksums, scope,
editions and each file's SHA-256, size and CSV row count. Package IDs are
`amr-reference-eucast` and `amr-reference-clsi`.

```text
manifest.json
configuration/backend/microbiology-organisms/organisms.csv
configuration/backend/microbiology-antimicrobials/antimicrobials.csv
configuration/backend/microbiology-breakpoints/breakpoints.csv
evidence/scope.json
evidence/coverage.csv
evidence/source-rows.csv
evidence/interpretation-vectors.csv
evidence/NOTICE.txt
```

Concept identity is `source=AMR` plus the source code. Display names and WHONET
codes are matching aids. Group rules expand into exact organism rules; this
package does not introduce a shared group fallback in OpenELIS.

Coverage records each context as SUPPORTED or BLOCKED with its source row IDs.
The source ledger accounts for all human rows in the selected editions. Numeric
comparison cases come from the source interpreter; blocked cases assert the
receiving application's No breakpoint behavior. Evidence stays outside the
runtime configuration scan. No executable code is installed.

Each edition version includes the package version, such as `2026-2026.1`.
The receiving application rejects changes to an existing managed edition. Changes
to unrelated release evidence do not change that edition's content identity.

## GHCR publication and deployment pins

The workflow builds each publisher twice and tests the results. It does not
upload CLSI files as public repository Actions artifacts. After merge, manually
run it on main with `publish` enabled, an explicit source commit and version.
It publishes OCI data artifacts with one `application/zip` layer using ORAS.
The publisher rejects an existing version tag and checks private visibility
before and after CLSI publication. CLSI requires an existing package whose
visibility is verified as private; source-repository inheritance can otherwise
create an internal package. For the first release, create an empty placeholder
with no restricted content, set its visibility to private in GitHub's package
settings, then run the publisher. Never change CLSI to public or internal visibility.

```sh
oras push --artifact-type application/vnd.openelis.registry-bootstrap.v1 \
  ghcr.io/digi-uw/amr-reference-clsi:registry-bootstrap
```

The placeholder contains no reference data and is not a deployment release.

For an authorized local release, authenticate `gh` and `oras` to GHCR, then run:

```sh
python3 data-raw/openelis/publish_package.py \
  dist/amr-reference-eucast-2026.1.zip \
  --repository ghcr.io/digi-uw/amr-reference-eucast
```

The command prints a JSON deployment pin with `id`, `version`, the immutable
`oci://ghcr.io/...@sha256:<manifest digest>` URL and the ZIP `sha256`. Commit that
actual pin in the distribution, never a placeholder or moving tag. Credentials
for private package reads belong in deployment secrets, never in the pin.

The PNG initializer verifies the manifest digest, layer digest, ZIP checksum and
file inventory, caches the archive, then installs ordinary configuration files.
It preserves local files and historical versions and reuses verified cache files
offline. The existing OpenELIS configuration loader imports concepts before
breakpoints and preserves referenced IDs and local customizations.

Before deployment, run the receiving Java compatibility test against each real
artifact, verify fresh loading and reload/upgrade, and inspect import reports.
New editions load inactive; activation is an explicit laboratory administration
step. HTTP health alone is not evidence that configuration loaded successfully.
No concept data belongs in Liquibase or the OpenELIS application image.
