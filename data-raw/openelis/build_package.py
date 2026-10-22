#!/usr/bin/env python3
"""Build a deterministic OpenELIS configuration artifact from pinned AMR source."""
import argparse
import csv
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import tarfile
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
R_IMAGE = (
    "r-base@sha256:fa1972f31def171b83e0911e947ab8b57db143f0fc8a67af4c0d5ac329041646"
)
FORMAT = "openelis-configuration/v1"
CONFIGURATION_PATHS = {
    "organisms.csv": "configuration/backend/microbiology-organisms/organisms.csv",
    "antimicrobials.csv": "configuration/backend/microbiology-antimicrobials/antimicrobials.csv",
    "breakpoints.csv": "configuration/backend/microbiology-breakpoints/breakpoints.csv",
}


def archive_path(name):
    return CONFIGURATION_PATHS.get(name, "evidence/" + name)



def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(value):
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def package_files(directory, metadata):
    payload = {
        p.name: p.read_bytes() for p in sorted(directory.iterdir()) if p.is_file()
    }
    files = {}
    for name, data in payload.items():
        item = {"sha256": sha256(data), "bytes": len(data)}
        if name.endswith(".csv"):
            item["rows"] = len(list(csv.DictReader(io.StringIO(data.decode("utf-8")))))
        files[archive_path(name)] = item
    payload = {archive_path(name): data for name, data in payload.items()}
    payload["manifest.json"] = canonical_json({**metadata, "files": files})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    return output.getvalue()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def validate_scope(scope):
    if set(scope) != {"guidelines", "organisms", "methods"}:
        raise ValueError("Scope requires guidelines, organisms and methods")
    if scope["methods"] != ["MIC", "ZONE"]:
        raise ValueError("Scope must account for MIC and ZONE contexts")
    if scope["guidelines"] not in (["EUCAST 2025", "EUCAST 2026"], ["CLSI 2025", "CLSI 2026"]):
        raise ValueError("Build each publisher separately for exactly 2025 and 2026")
    if scope["organisms"] != "human-breakpoint-taxa":
        raise ValueError("Scope requires human-breakpoint-taxa")


def build(args):
    scope = json.loads(args.scope.read_text())
    validate_scope(scope)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_ref):
        raise ValueError(
            "source-ref must be a full lowercase 40-character commit, not a moving ref"
        )
    if len(args.package_version) > 40 or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+){1,2}", args.package_version
    ):
        raise ValueError("package-version must be a numeric release such as 2026.1")
    commit = (
        git("rev-parse", "--verify", args.source_ref + "^{commit}").decode().strip()
    )
    description = git("show", commit + ":DESCRIPTION").decode()
    version = re.search(r"^Version: (.+)$", description, re.MULTILINE).group(1)
    with tempfile.TemporaryDirectory(prefix="openelis-reference-") as work:
        work = pathlib.Path(work)
        source = work / "source"
        source.mkdir()
        archive = git(
            "archive", commit, "DESCRIPTION", "NAMESPACE", "R", "data", "inst"
        )
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(source, filter="data")
        output = work / "artifact"
        output.mkdir()
        (work / "library").mkdir()
        (work / "organisms.txt").write_text(
            scope["organisms"] + "\n"
        )
        (work / "guidelines.txt").write_text(
            "\n".join(sorted(set(scope["guidelines"]))) + "\n"
        )
        if args.runtime == "docker":
            mount = [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-v",
                f"{work}:/work",
                "-v",
                f"{HERE}:/exporter:ro",
                R_IMAGE,
            ]
            subprocess.run(
                mount
                + [
                    "R",
                    "CMD",
                    "INSTALL",
                    "--no-docs",
                    "--no-html",
                    "--library=/work/library",
                    "/work/source",
                ],
                check=True,
            )
            subprocess.run(
                mount
                + [
                    "Rscript",
                    "--vanilla",
                    "/exporter/export.R",
                    "/work/library",
                    "/work/organisms.txt",
                    "/work/guidelines.txt",
                    args.package_version,
                    "/work/artifact",
                ],
                check=True,
            )
        else:
            subprocess.run(
                [
                    "R",
                    "CMD",
                    "INSTALL",
                    "--no-docs",
                    "--no-html",
                    "--library=" + str(work / "library"),
                    str(source),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "Rscript",
                    "--vanilla",
                    str(HERE / "export.R"),
                    str(work / "library"),
                    str(work / "organisms.txt"),
                    str(work / "guidelines.txt"),
                    args.package_version,
                    str(output),
                ],
                check=True,
            )
        (output / "scope.json").write_bytes(canonical_json(scope))
        (output / "NOTICE.txt").write_text(
            "Source: DIGI-UW/AMR, derived from AMR for R (GPL-2.0).\n"
            "Clinical breakpoint source: " + scope["guidelines"][0].split()[0] + "; retain its attribution and usage terms.\n"
            "https://amr-for-r.org/reference/clinical_breakpoints.html\n"
            "https://www.eucast.org/clinical_breakpoints\n"
            "This is a scoped testing reference package, not the complete guideline or clinical approval.\n"
            "Only unconditional MIC S/I/R rules supported by the receiving contract are included.\n"
            "Unsupported disk, site, route, UTI, SDD and conditional contexts load as explicit No breakpoint rules.\n"
            "The AMR package documents public distribution of its EUCAST and CLSI breakpoint datasets.\n"
            "Berends MS et al. (2022), Journal of Statistical Software 104(3), 1-31.\n"
            "https://doi.org/10.18637/jss.v104.i03\n"
            "Exact source: https://github.com/DIGI-UW/AMR/tree/" + commit + "\n"
            "Coverage and source-rows CSVs account for exclusions. New editions load inactive.\n\n"
            + git("show", commit + ":LICENSE").decode("utf-8"),
            encoding="utf-8",
        )
        metadata = {
            "format": FORMAT,
            "runtime": {
                "mode": args.runtime,
                "image": R_IMAGE if args.runtime == "docker" else None,
            },
            "packageId": "amr-reference-" + scope["guidelines"][0].split()[0].lower(),
            "packageVersion": args.package_version,
            "source": {
                "repository": "https://github.com/DIGI-UW/AMR",
                "commit": commit,
                "packageVersion": version,
                "dataSha256": {
                    name: sha256(git("show", commit + ":data/" + name + ".rda"))
                    for name in [
                        "microorganisms",
                        "microorganisms.codes",
                        "microorganisms.groups",
                        "antimicrobials",
                        "clinical_breakpoints",
                    ]
                },
            },
            "exporterSha256": {
                name: sha256((HERE / name).read_bytes())
                for name in ["build_package.py", "export.R", "validate_package.py"]
            },
            "scope": scope,
            "standards": [
                {
                    "publisher": x.split()[0],
                    "version": x.split()[1] + "-" + args.package_version,
                    "guideline": x,
                }
                for x in sorted(set(scope["guidelines"]))
            ],
        }
        content = package_files(output, metadata)
        from validate_package import validate

        counts = validate(content)
        print(json.dumps({"bytes": len(content), **counts}, sort_keys=True), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and args.output.read_bytes() != content:
        raise ValueError(
            "Refusing to replace an existing artifact with different content"
        )
    args.output.write_bytes(content)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        sha256(content) + "  " + args.output.name + "\n"
    )
    print(f"{args.output}: sha256:{sha256(content)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-ref", required=True, help="Full immutable AMR source commit"
    )
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--scope", type=pathlib.Path, default=HERE / "scope.json")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--runtime", choices=["local", "docker"], default="docker")
    build(parser.parse_args())
