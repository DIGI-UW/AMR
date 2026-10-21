"""Distribution contract tests; set AMR_REFERENCE_ARTIFACT for the real-source cases."""

import csv
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
import zipfile

from build_package import archive_path, package_files, validate_scope
from validate_package import validate


class ScopeTest(unittest.TestCase):
    def test_only_explicit_two_editions_are_accepted(self):
        scope = json.loads(Path(__file__).with_name("scope.json").read_text())
        validate_scope(scope)
        for guidelines in (
            ["EUCAST 2024", "EUCAST 2025"],
            ["CLSI 2025", "CLSI 2026"],
            ["EUCAST 2026"],
            ["latest"],
        ):
            with self.subTest(guidelines=guidelines), self.assertRaises(ValueError):
                validate_scope({**scope, "guidelines": guidelines})

    def test_moving_source_ref_is_rejected_before_starting_r(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("build_package.py")),
                    "--source-ref",
                    "main",
                    "--package-version",
                    "2026.1",
                    "--runtime",
                    "docker",
                    "--output",
                    str(Path(directory) / "data.zip"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("not a moving ref", result.stderr)
            self.assertFalse((Path(directory) / "data.zip").exists())

    def test_methods_and_duplicate_identities_cannot_be_silently_coerced(self):
        scope = json.loads(Path(__file__).with_name("scope.json").read_text())
        for changes in (
            {"methods": ["DISK"]},
            {"organisms": ["B_ESCHR_COLI", "B_ESCHR_COLI"]},
            {"organisms": []},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_scope({**scope, **changes})


@unittest.skipUnless(
    os.environ.get("AMR_REFERENCE_ARTIFACT"),
    "Build an artifact and set AMR_REFERENCE_ARTIFACT",
)
class ArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = Path(os.environ["AMR_REFERENCE_ARTIFACT"]).read_bytes()
        with zipfile.ZipFile(io.BytesIO(cls.content)) as archive:
            cls.payload = {Path(name).name: archive.read(name) for name in archive.namelist()}
        cls.metadata = json.loads(cls.payload["manifest.json"])
        del cls.metadata["files"]

    def repack(self, mutate):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for name, data in self.payload.items():
                if name != "manifest.json":
                    (directory / name).write_bytes(data)
            mutate(directory)
            return package_files(directory, self.metadata)

    @staticmethod
    def change_csv(directory, name, mutate):
        path = directory / name
        reader = csv.DictReader(io.StringIO(path.read_text()))
        fields, rows = reader.fieldnames, list(reader)
        mutate(rows)
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_real_source_is_accounted_for(self):
        counts = validate(self.content)
        self.assertEqual(5, counts["organisms"])
        self.assertGreater(counts["rules"], 0)
        self.assertGreater(counts["unsupportedContexts"], counts["rules"])
        self.assertGreater(counts["vectors"], counts["rules"] * 4)
        self.assertEqual(
            {"EUCAST 2025", "EUCAST 2026"},
            {
                x["guideline"]
                for x in json.loads(self.payload["manifest.json"])["standards"]
            },
        )

    def test_zip_is_reproducible_despite_file_times_and_order(self):
        rebuilt = self.repack(
            lambda directory: [
                os.utime(path, (12345, 12345))
                for path in reversed(list(directory.iterdir()))
            ]
        )
        self.assertEqual(self.content, rebuilt)

    def test_strict_boundary_cannot_become_inclusive(self):
        def mutate(rows):
            rows[0]["resistant_inclusive"] = "true"

        content = self.repack(lambda d: self.change_csv(d, "breakpoints.csv", mutate))
        with self.assertRaisesRegex(ValueError, "strict resistant"):
            validate(content)

    def test_wrong_interpretation_fails_even_with_valid_checksums(self):
        def mutate(rows):
            rows[0]["expected"] = "R" if rows[0]["expected"] == "S" else "S"

        content = self.repack(
            lambda d: self.change_csv(d, "interpretation-vectors.csv", mutate)
        )
        with self.assertRaisesRegex(ValueError, "differs from translated"):
            validate(content)

    def test_unsupported_context_cannot_gain_a_numeric_rule(self):
        def mutate(rows):
            row = next(row for row in rows if row["status"] == "SUPPORTED")
            row.update(status="UNSUPPORTED", reason="conditional_note_requires_review")

        content = self.repack(lambda d: self.change_csv(d, "coverage.csv", mutate))
        with self.assertRaisesRegex(ValueError, "Unsupported context has a rule"):
            validate(content)

    def test_missing_boundary_is_rejected(self):
        def mutate(directory):
            def remove_boundary(rows):
                first = rows[0]
                # Removing a whole rule's vectors must fail despite intact rule and coverage files.
                rows[:] = [
                    row
                    for row in rows
                    if any(
                        row[key] != first[key]
                        for key in (
                            "version",
                            "organism_code",
                            "antibiotic_code",
                            "method",
                        )
                    )
                ]

            self.change_csv(directory, "interpretation-vectors.csv", remove_boundary)

        with self.assertRaisesRegex(ValueError, "Missing boundary"):
            validate(self.repack(mutate))

    def test_duplicate_concepts_fail_before_loading(self):
        content = self.repack(
            lambda d: self.change_csv(
                d, "organisms.csv", lambda rows: rows.append(rows[0])
            )
        )
        with self.assertRaisesRegex(ValueError, "Organism inventory"):
            validate(content)

    def test_unaccounted_source_row_fails(self):
        def mutate(rows):
            next(row for row in rows if row["status"] == "SEE_COVERAGE")[
                "status"
            ] = "OUTSIDE_ORGANISM_SCOPE"

        content = self.repack(lambda d: self.change_csv(d, "source-rows.csv", mutate))
        with self.assertRaisesRegex(ValueError, "Incomplete source"):
            validate(content)

    def test_path_or_duplicate_entries_fail(self):
        for extra in ("../outside", archive_path("organisms.csv")):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                for name, data in self.payload.items():
                    archive.writestr(archive_path(name) if name != "manifest.json" else name, data)
                archive.writestr(extra, b"extra")
            with self.subTest(extra=extra), self.assertRaisesRegex(
                ValueError, "inventory"
            ):
                validate(output.getvalue())

    def test_tampering_is_detected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, data in self.payload.items():
                archive.writestr(
                    archive_path(name) if name != "manifest.json" else name, data + b"\n" if name == "breakpoints.csv" else data
                )
        with self.assertRaisesRegex(ValueError, "Checksum"):
            validate(output.getvalue())


if __name__ == "__main__":
    unittest.main()
