#!/usr/bin/env python3
"""Check the OpenELIS v1 archive contract without R or an OpenELIS installation."""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import re
import zipfile
from build_package import archive_path, FORMAT

FILES = {
    "organisms.csv",
    "antimicrobials.csv",
    "breakpoints.csv",
    "interpretation-vectors.csv",
    "coverage.csv",
    "source-rows.csv",
    "scope.json",
    "NOTICE.txt",
    "manifest.json",
}
MAX_BYTES = 32 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def number(value):
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid numeric threshold or observation") from error
    require(result.is_finite() and result > 0, "Expected a finite positive number")
    return result


def validate(content):
    require(len(content) <= MAX_BYTES, "Archive exceeds size limit")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        require(
            set(names) == {archive_path(n) if n != "manifest.json" else n for n in FILES} and len(names) == len(FILES),
            "Unexpected archive inventory",
        )
        require(
            sum(item.file_size for item in archive.infolist()) <= MAX_BYTES,
            "Expanded archive exceeds size limit",
        )
        payload = {name: archive.read(archive_path(name) if name != "manifest.json" else name) for name in FILES}
    manifest = json.loads(payload["manifest.json"])
    require(manifest["format"] == FORMAT, "Unknown format")
    require(
        re.fullmatch(r"[0-9a-f]{40}", manifest["source"]["commit"]),
        "Source commit is not pinned",
    )
    require(
        re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", manifest["packageVersion"]),
        "Invalid package version",
    )
    require(
        manifest["packageId"] == "amr-reference",
        "Package identity mismatch",
    )
    require(
        set(manifest["files"]) == {archive_path(n) for n in FILES - {"manifest.json"}},
        "Manifest inventory mismatch",
    )
    tables = {}
    for name in FILES - {"manifest.json"}:
        item = manifest["files"][archive_path(name)]
        data = payload[name]
        require(
            item["sha256"] == digest(data) and item["bytes"] == len(data),
            "Checksum mismatch: " + name,
        )
        if name.endswith(".csv"):
            reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
            require(
                reader.fieldnames
                and len(reader.fieldnames) == len(set(reader.fieldnames)),
                "Duplicate CSV columns",
            )
            tables[name] = list(reader)
            require(
                all(
                    None not in row and None not in row.values() for row in tables[name]
                ),
                "Malformed CSV row",
            )
            require(item["rows"] == len(tables[name]), "Row count mismatch: " + name)
    scope = json.loads(payload["scope.json"])
    require(scope == manifest["scope"], "Scope differs from manifest")
    require(
        scope["guidelines"] == ["EUCAST 2025", "EUCAST 2026"]
        and scope["methods"] == ["MIC"],
        "Unsupported release scope",
    )
    organisms = tables["organisms.csv"]
    agents = tables["antimicrobials.csv"]
    organism_codes = {row["code"] for row in organisms}
    agent_codes = {row["code"] for row in agents}
    require(
        len(organism_codes) == len(organisms)
        and organism_codes == set(scope["organisms"]),
        "Organism inventory mismatch",
    )
    require(
        len(agent_codes) == len(agents) and agents,
        "Duplicate or empty antimicrobial inventory",
    )
    require(
        all(
            row["source"] == "AMR" and row["displayName"] for row in organisms + agents
        ),
        "Invalid source identity",
    )
    editions = {row["guideline"]: row["version"] for row in manifest["standards"]}
    require(
        len(manifest["standards"]) == 2 and set(editions) == set(scope["guidelines"]),
        "Edition inventory mismatch",
    )
    require(
        all(
            row["publisher"] == "EUCAST"
            and row["version"]
            == row["guideline"].split()[1] + "-" + manifest["packageVersion"]
            for row in manifest["standards"]
        ),
        "Edition identity mismatch",
    )
    rules = {}
    for row in tables["breakpoints.csv"]:
        key = (
            row["version"],
            row["organism_code"],
            row["antibiotic_code"],
            row["method"],
        )
        require(key not in rules, "Duplicate breakpoint")
        require(
            row["publisher"] == "EUCAST" and row["version"] in editions.values(),
            "Unexpected standard",
        )
        require(
            row["organism_source"] == row["antibiotic_source"] == "AMR",
            "Unexpected reference source",
        )
        require(
            row["organism_code"] in organism_codes
            and row["antibiotic_code"] in agent_codes,
            "Unresolved concept",
        )
        require(
            row["method"] == row["breakpoint_type"] == "MIC" and row["units"] == "mg/L",
            "Unsupported method or units",
        )
        require(
            row["resistant_inclusive"] == "false",
            "EUCAST MIC requires a strict resistant boundary",
        )
        require(
            not any(
                row[name]
                for name in (
                    "specimen_type_id",
                    "intermediate_lower_value",
                    "intermediate_upper_value",
                )
            ),
            "Unsupported context",
        )
        susceptible, resistant = number(row["susceptible_value"]), number(
            row["resistant_value"]
        )
        require(
            Decimal("0.001") < susceptible <= resistant, "Invalid supported thresholds"
        )
        require(
            all(
                value.as_tuple().exponent >= -4 and value < 10**8
                for value in (susceptible, resistant)
            ),
            "Threshold exceeds receiving precision",
        )
        rules[key] = (susceptible, resistant)
    require(
        rules and {key[0] for key in rules} == set(editions.values()), "Empty edition"
    )
    observations = {key: set() for key in rules}
    for row in tables["interpretation-vectors.csv"]:
        key = (
            row["version"],
            row["organism_code"],
            row["antibiotic_code"],
            row["method"],
        )
        require(row["publisher"] == "EUCAST" and key in rules, "Vector without a rule")
        value = number(row["value"])
        require(value not in observations[key], "Duplicate vector")
        observations[key].add(value)
        susceptible, resistant = rules[key]
        expected = "S" if value <= susceptible else "R" if value > resistant else "I"
        require(
            row["expected"] == expected, "AMR vector differs from translated threshold"
        )
    for key, values in observations.items():
        susceptible, resistant = rules[key]
        require(
            susceptible in values
            and resistant in values
            and any(x < susceptible for x in values)
            and any(x > resistant for x in values),
            "Missing boundary vectors",
        )
        require(
            susceptible == resistant
            or any(susceptible < x <= resistant for x in values),
            "Missing intermediate vector",
        )
    source_rows = tables["source-rows.csv"]
    source_ids = {row["source_row"] for row in source_rows}
    require(len(source_ids) == len(source_rows), "Duplicate source row")
    require(
        all(
            row["guideline"] in editions
            and row["status"] in {"SEE_COVERAGE", "OUTSIDE_ORGANISM_SCOPE"}
            for row in source_rows
        ),
        "Unexpected source scope",
    )
    coverage_keys, supported, referenced = set(), set(), set()
    for row in tables["coverage.csv"]:
        key = (
            editions[row["guideline"]],
            row["organism_code"],
            row["antibiotic_code"],
            row["method"],
        )
        require(key not in coverage_keys, "Duplicate coverage context")
        coverage_keys.add(key)
        require(
            row["organism_code"] in organism_codes
            and row["antibiotic_code"] in agent_codes,
            "Unknown coverage concept",
        )
        ids = set(row["source_rows"].split(";"))
        require(ids <= source_ids, "Unknown source row in coverage")
        referenced.update(ids)
        if row["status"] == "SUPPORTED":
            require(
                key in rules and not row["reason"], "Supported coverage without a rule"
            )
            supported.add(key)
        else:
            require(
                row["status"] == "UNSUPPORTED" and row["reason"] and key not in rules,
                "Unsupported context has a rule",
            )
    require(supported == set(rules), "Rule missing supported coverage")
    require(
        referenced
        == {
            row["source_row"] for row in source_rows if row["status"] == "SEE_COVERAGE"
        },
        "Incomplete source accounting",
    )
    return {
        "organisms": len(organisms),
        "antimicrobials": len(agents),
        "rules": len(rules),
        "vectors": len(tables["interpretation-vectors.csv"]),
        "unsupportedContexts": len(coverage_keys - supported),
    }


if __name__ == "__main__":
    import pathlib

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument(
        "--sha256", help="Expected archive SHA-256 from the deployment pin"
    )
    args = parser.parse_args()
    content = args.artifact.read_bytes()
    if args.sha256:
        require(digest(content) == args.sha256, "Archive differs from deployment pin")
    print(json.dumps(validate(content), sort_keys=True))
